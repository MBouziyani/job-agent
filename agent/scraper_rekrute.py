"""
ReKrute Scraper — Moroccan job board (Maroc's biggest).

Uses Playwright (headless Chromium, 16GB RAM server) to render the AngularJS
pages that block plain HTTP scraping, then extracts real job offers:
company, title, location, offer URL.

The companies found here are MULTINATIONAL + local — exactly the target
list for the Morocco outreach track (they post on ReKrute to hire in Morocco).
"""
import logging
import re
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

REKRUTE_BASE = 'https://www.rekrute.com'
REKRUTE_SEARCH = 'https://www.rekrute.com/offres.html?keyword={keyword}'
KEYWORDS = ['developpeur', 'informatique', 'data', 'devops', 'fullstack', 'java']

OFFER_RE = re.compile(r'/(offre-emploi-[a-z0-9-]+\.html)')
TITLE_RE = re.compile(r'<a[^>]*href="[^"]*offre-emploi-[^"]*\.html"[^>]*>\s*(.*?)</a>', re.S)


def _clean(text: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', text)).strip()


def _scrape_keyword(keyword: str) -> list[dict]:
    """Scrape one keyword search page with Playwright."""
    offers: dict[str, dict] = {}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error('Playwright not installed — cannot scrape ReKrute')
        return []

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
            page = browser.new_page(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            page.goto(REKRUTE_SEARCH.format(keyword=keyword), wait_until='domcontentloaded', timeout=60000)
            page.wait_for_timeout(5000)
            html = page.content()

            # Extract offer links
            for m in OFFER_RE.finditer(html):
                path = m.group(1)
                url = f'{REKRUTE_BASE}/{path}'
                offers.setdefault(url, {'url': url})

            # Extract titles (match links to text)
            for m in TITLE_RE.finditer(html):
                title = _clean(m.group(1))
                # find which URL this title belongs to — the nearest preceding offer link
                pass

            # Parse title/company/location from the URL slug itself (very reliable)
            for url in offers:
                slug = url.rstrip('.html').split('/')[-1]
                # slug pattern: offre-emploi-{title}-recrutement-{company}-{city}-{id}
                m = re.search(r'offre-emploi-(.+)-recrutement-([a-z0-9-]+?)-([a-z-]+)-(\d+)$', slug)
                if m:
                    title_slug, company, city, oid = m.groups()
                    offers[url].update({
                        'title': title_slug.replace('-', ' ').title(),
                        'company': company.replace('-', ' ').title(),
                        'city': city.replace('-', ' ').title(),
                        'offer_id': oid,
                    })
            browser.close()
    except Exception as exc:
        logger.error('ReKrute Playwright error (%s): %s', keyword, exc)
        return []

    return list(offers.values())


def scrape_rekrute() -> list[dict]:
    """Scrape all keywords, dedupe offers."""
    all_offers: dict[str, dict] = {}
    for kw in KEYWORDS:
        offers = _scrape_keyword(kw)
        logger.info('ReKrute "%s": %d offers', kw, len(offers))
        for o in offers:
            all_offers.setdefault(o['url'], o)
    logger.info('ReKrute total: %d unique offers', len(all_offers))
    return list(all_offers.values())


def run(conn: sqlite3.Connection, cfg: dict[str, Any]) -> dict[str, int]:
    """Scrape ReKrute, insert companies + contacts into DB."""
    offers = scrape_rekrute()
    inserted_companies = 0
    inserted_contacts = 0

    for offer in offers:
        company = offer.get('company')
        if not company or not offer.get('url'):
            continue

        # Company exists? (by name)
        cur = conn.execute("SELECT id FROM companies WHERE name = ?", (company,))
        row = cur.fetchone()
        if row:
            company_id = row[0]
        else:
            cur = conn.execute(
                "INSERT INTO companies (name, source, qualified, remote_score) "
                "VALUES (?, 'rekrute', NULL, 0)",
                (company,),
            )
            company_id = cur.lastrowid
            inserted_companies += 1

        # Contact = recruiting email if present, else the offer URL as context
        # ReKrute offers show company pages — try company domain later via finder
        cur = conn.execute(
            "SELECT id FROM contacts WHERE company_id = ? AND source = 'rekrute'",
            (company_id,),
        )
        if not cur.fetchone():
            conn.execute(
                "INSERT INTO contacts (company_id, name, role, email, source, verified) "
                "VALUES (?, ?, 'Recruiting (ReKrute)', NULL, 'rekrute', 0)",
                (company_id, offer.get('title')),
            )
            inserted_contacts += 1

    conn.commit()
    logger.info('ReKrute done — companies=%d contacts=%d', inserted_companies, inserted_contacts)
    return {'companies': inserted_companies, 'contacts': inserted_contacts}
