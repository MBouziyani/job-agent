"""
Website Contact Finder — scrapes company websites for published contact emails.

Replaces pattern-guessing (first@domain.com) with REAL emails found on the
company's own site: homepage, /contact, /about, /team, /about-us, /leadership.
Uses 16GB RAM: parallel fetches across companies.

Inserted contacts are marked verified=1 (they came from the company's own
published pages, so the domain definitely exists and accepts mail there).
"""
import logging
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

logger = logging.getLogger(__name__)

TIMEOUT = 12
MAX_WORKERS = 20  # 16GB RAM — plenty for 20 parallel fetches
USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36'

# Pages likely to list a person's email
PAGE_CANDIDATES = [
    '',            # homepage
    '/contact', '/contact-us', '/contactus', '/about', '/about-us',
    '/team', '/our-team', '/about/team', '/leadership', '/company/team',
    '/company/about', '/company', '/people', '/founders', '/about/leadership',
    '/meet-the-team', '/the-team', '/who-we-are', '/company/leadership',
    '/about/team', '/about-us/team', '/management', '/our-people',
    # Careers pages — recruiting emails live here
    '/careers', '/jobs', '/join-us', '/join', '/work-with-us', '/work-withus',
    '/career', '/openings', '/positions', '/hiring', '/about/careers',
    '/company/careers', '/company/jobs', '/careers/join-us', '/careers/openings',
    '/about-us/careers', '/job-openings', '/apply', '/recruiting',
]

# Recruiting inboxes — these are PUBLISHED on purpose to receive applications
RECRUITING_PREFIXES = (
    'jobs@', 'careers@', 'career@', 'talent@', 'recruiting@', 'recruit@',
    'hiring@', 'join@', 'joinus@', 'apply@', 'people@', 'hr@', 'work@',
    'workwithus@', 'job@', 'vacancies@', 'hiring@', 'openhiring@', 'careers',
)

# Generic inboxes — published but low-value for job outreach
GENERIC_PREFIXES = (
    'hello@', 'info@', 'contact@', 'support@', 'press@', 'media@', 'admin@',
    'mail@', 'noreply@', 'no-reply@', 'sales@', 'marketing@', 'pr@', 'legal@',
    'abuse@', 'billing@', 'team@', 'hi@', 'hey@', 'office@', 'social@',
)

EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
NAME_RE = re.compile(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})')


def _fetch(url: str) -> str | None:
    """Fetch a page, return text (lowercased not here — keep case for names)."""
    try:
        resp = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=TIMEOUT, verify=False)
        if resp.status_code == 200 and 'text/html' in resp.headers.get('content-type', ''):
            return resp.text
    except Exception:
        pass
    return None


def _extract_emails(html: str, domain: str) -> set[str]:
    """Extract emails whose domain matches the company (avoid junk from CDNs etc)."""
    found = set()
    for m in EMAIL_RE.findall(html):
        email = m.strip('.').strip(')')
        local, _, dm = email.partition('@')
        if not local or not dm:
            continue
        # Only keep emails on the company's own domain (or common personal-hosted)
        if dm.lower() == domain.lower() or dm.lower().endswith('.' + domain.lower()):
            found.add(email)

    # mailto: links (often contain the real person's address)
    for m in re.finditer(r'mailto:([^"\'>\s?]+)', html, re.I):
        email = m.group(1).strip()
        if '@' in email:
            found.add(email)

    # Obfuscated: name [at] domain [dot] com / name(at)domain(dot)com
    for m in re.finditer(
        r'([a-zA-Z0-9._%+-]+)\s*(?:\[at\]|\(at\)|\s*@\s*)\s*'
        r'([a-zA-Z0-9.-]+)\s*(?:\[dot\]|\(dot\)|\.)\s*([a-z]{2,})',
        html, re.I,
    ):
        local, dm, tld = m.group(1), m.group(2).lower(), m.group(3).lower()
        if tld not in ('com', 'org', 'net', 'io', 'dev', 'co', 'ai', 'app', 'me', 'tech', 'jobs', 'xyz', 'cloud', 'work', 'careers'):
            continue
        full = f'{local}@{dm}.{tld}'
        if dm == domain.lower() or dm.endswith('.' + domain.lower()):
            found.add(full)
    return found


def _guess_name_near_email(html: str, email: str) -> str | None:
    """Try to find a person's name near the email in the page."""
    idx = html.find(email)
    if idx == -1:
        return None
    window = html[max(0, idx - 400): idx + 400]
    # Common patterns: "John Smith john@domain.com" / "john@domain.com John Smith"
    names = NAME_RE.findall(window)
    if names:
        # Skip if the name is actually inside the email domain text
        for n in names:
            if email not in n and n not in email:
                return n
    return None


def _crawl_domain(domain: str) -> list[dict]:
    """Fetch the company's site, find published emails, return contact dicts."""
    domain = domain.lower().strip().rstrip('/')
    if not domain or '.' not in domain:
        return []

    # Try https first, fall back to http
    scheme = 'https'
    probe = _fetch(f'https://{domain}')
    if probe is None:
        probe = _fetch(f'http://{domain}')
        scheme = 'http'
    if probe is None:
        return []

    # Build page list: homepage nav links first (they're the real ones),
    # then fall back to common paths only if nav didn't reveal much.
    pages: list[str] = []
    seen_urls: set[str] = set()

    for href in re.findall(r'href="(/[^"]*)"', probe, re.I):
        path = href.split('#')[0].split('?')[0].lower()
        if not path or path == '/':
            continue
        if any(k in path for k in ('contact', 'career', 'job', 'team', 'people',
                                   'about', 'join', 'hiring', 'talent', 'work',
                                   'founder', 'leadership', 'recruit', 'open')):
            url = f'{scheme}://{domain}{href}' if href.startswith('/') else href
            if url not in seen_urls:
                seen_urls.add(url)
                pages.append(url)
        if len(pages) >= 8:
            break

    # Fallback: common paths if nav didn't give us anything useful
    if len(pages) < 3:
        for path in PAGE_CANDIDATES:
            if path:
                url = f'{scheme}://{domain}{path}'
                if url not in seen_urls:
                    seen_urls.add(url)
                    pages.append(url)
            if len(pages) >= 8:
                break

    emails: dict[str, str | None] = {}  # email -> name
    for url in pages:
        html = _fetch(url)
        if not html:
            continue
        for email in _extract_emails(html, domain):
            if email.lower() in emails:
                continue
            name = _guess_name_near_email(html, email)
            emails[email.lower()] = name
        time.sleep(0.05)  # polite

    # Convert to contact dicts, prefer named/person emails
    contacts = []
    for email, name in emails.items():
        if not name:
            # Try to derive a name from local part (john.smith -> John Smith)
            local = email.split('@')[0]
            parts = re.split(r'[._\-+]', local)
            if len(parts) >= 2 and all(p.isalpha() and len(p) > 1 for p in parts):
                name = ' '.join(p.capitalize() for p in parts)
        role = _infer_role(email, name, '')
        contacts.append({'email': email, 'name': name, 'role': role})
    return contacts


def _infer_role(email: str, name: str | None, context: str) -> str:
    """Guess the role from email local part conventions."""
    local = email.split('@')[0].lower()
    if any(k in local for k in ('cto', 'chief', 'tech')):
        return 'CTO'
    if any(k in local for k in ('founder', 'ceo', 'president')):
        return 'Founder / CEO'
    if any(k in local for k in ('vp', 'vice')):
        return 'VP Engineering'
    if any(k in local for k in ('engineer', 'dev', 'fullstack', 'software')):
        return 'Engineer'
    if any(k in local for k in ('hr', 'talent', 'recruit')):
        return 'HR / Talent'
    return 'Team Member'


def run(conn: sqlite3.Connection, cfg: dict[str, Any]) -> dict[str, int]:
    """Scrape company websites for published emails. Insert as verified contacts."""

    # Companies: qualified, have a domain, and either no contact or only unverified ones
    cur = conn.execute("""
        SELECT DISTINCT c.id, c.name, c.domain
        FROM companies c
        WHERE c.qualified = 1
          AND c.domain IS NOT NULL AND c.domain != ''
          AND NOT EXISTS (
              SELECT 1 FROM contacts ct
              WHERE ct.company_id = c.id AND ct.verified = 1
          )
        ORDER BY c.remote_score DESC
        LIMIT 40
    """)
    rows = cur.fetchall()
    companies = [{"id": r[0], "name": r[1], "domain": r[2]} for r in rows]
    logger.info('Website finder: %d companies to scrape', len(companies))
    if not companies:
        return {'scraped': 0, 'found': 0, 'inserted': 0}

    inserted = 0
    found_total = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        fut_map = {pool.submit(_crawl_domain, c['domain']): c for c in companies}
        for f in as_completed(fut_map):
            company = fut_map[f]
            try:
                contacts = f.result()
            except Exception as exc:
                logger.debug('Website finder error for %s: %s', company['domain'], exc)
                contacts = []
            if not contacts:
                logger.info('Website: %s — no emails found', company['name'])
                continue
            found_total += len(contacts)
            # Priority: recruiting inboxes first (published on purpose for applications)
            contacts.sort(key=lambda c: (
                0 if c['email'].lower().startswith(RECRUITING_PREFIXES) else 1,
                c['email'].lower().startswith(GENERIC_PREFIXES),
            ))
            for c in contacts:
                # Skip generic role inboxes (hello@, info@...) — recruiting kept
                if c['email'].lower().startswith(GENERIC_PREFIXES):
                    continue
                # Dedupe within company
                exists = conn.execute(
                    "SELECT id FROM contacts WHERE company_id = ? AND email = ?",
                    (company['id'], c['email']),
                ).fetchone()
                if exists:
                    # Mark existing unverified as verified now
                    conn.execute(
                        "UPDATE contacts SET verified = 1 WHERE id = ?", (exists[0],)
                    )
                    continue
                # Recruiting emails are verified — published on the company site
                is_recruiting = c['email'].lower().startswith(RECRUITING_PREFIXES)
                role = c['role'] or ('Recruiting / HR' if is_recruiting else 'Team Member')
                conn.execute(
                    "INSERT INTO contacts (company_id, name, role, email, source, verified) "
                    "VALUES (?, ?, ?, ?, 'website', 1)",
                    (company['id'], c['name'], role, c['email']),
                )
                inserted += 1
            logger.info(
                'Website: %s — %d emails found, %d new (%s)',
                company['name'], len(contacts), inserted if inserted else 'see total',
                ', '.join(c['email'] for c in contacts[:3]),
            )
            conn.commit()

    conn.commit()
    logger.info(
        'Website finder done — scraped=%d found=%d inserted=%d',
        len(companies), found_total, inserted,
    )
    return {'scraped': len(companies), 'found': found_total, 'inserted': inserted}
