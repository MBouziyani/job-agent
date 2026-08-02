"""
Open Jobs Finder (Greenhouse / Lever / Ashby)

Probes our qualified companies' ATS boards via official public APIs (free, no
auth) and pulls their open roles into a new `open_jobs` table so the dashboard
can show real, applyable jobs — no scraping, no ban risk.

ATS detection: try the company's domain as board slug; also common aliases.
"""
import logging
import re
import sqlite3
from typing import Any

import requests

logger = logging.getLogger(__name__)

HEADERS = {'User-Agent': 'Mozilla/5.0 (job-agent/1.0)'}
TIMEOUT = 8

# How many companies to probe per run (rate-limit friendly)
DAILY_LIMIT = 100

GREENHOUSE_URL = 'https://boards-api.greenhouse.io/v1/boards/{slug}/jobs'
LEVER_URL = 'https://api.lever.co/v0/postings/{slug}?mode=json'
ASHBY_URL = 'https://api.ashbyhq.com/posting-api/job-board/{slug}'

# Roles the candidate can fill — he said "apply to everything", so be generous
MATCH_KEYWORDS = [
    'engineer', 'developer', 'software', 'fullstack', 'full-stack', 'frontend',
    'front-end', 'backend', 'back-end', 'devops', 'sre', 'data', 'python',
    'javascript', 'typescript', 'react', 'node', 'java', 'php', 'web',
    'platform', 'cloud', 'infrastructure', 'qa', 'test', 'product', 'support',
    'solutions', 'technical', 'automation', 'ml', 'ai', 'devrel', 'developer relations',
]


def _board_slugs(domain: str, name: str) -> list[str]:
    """Candidate ATS slugs for a company."""
    slugs = []
    if domain:
        d = domain.lower().strip().rstrip('/')
        # strip leading www.
        d = re.sub(r'^www\.', '', d)
        base = d.split('.')[0] if '.' in d else d
        slugs.append(base)
    # from company name: first word lowercase alnum
    if name:
        w = re.sub(r'[^a-z0-9]', '', name.lower().split()[0]) if name.split() else ''
        if w and w not in slugs:
            slugs.append(w)
    return slugs


def _probe_greenhouse(slug: str) -> list[dict] | None:
    try:
        r = requests.get(GREENHOUSE_URL.format(slug=slug), headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        data = r.json()
        jobs = []
        for j in data.get('jobs', []):
            loc = (j.get('location') or {}).get('name', '')
            jobs.append({
                'title': j.get('title', ''),
                'location': loc,
                'url': j.get('absolute_url', ''),
                'dept': (j.get('departments') or [{}])[0].get('name', '') if j.get('departments') else '',
                'board': 'greenhouse',
                'board_slug': slug,
            })
        return jobs
    except Exception:
        return None


def _probe_lever(slug: str) -> list[dict] | None:
    try:
        r = requests.get(LEVER_URL.format(slug=slug), headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        data = r.json()
        if not isinstance(data, list):
            return None
        jobs = []
        for j in data:
            jobs.append({
                'title': j.get('text', ''),
                'location': (j.get('categories') or {}).get('location', ''),
                'url': j.get('hostedUrl', ''),
                'dept': (j.get('categories') or {}).get('commitment', ''),
                'board': 'lever',
                'board_slug': slug,
            })
        return jobs
    except Exception:
        return None


def _probe_ashby(slug: str) -> list[dict] | None:
    try:
        r = requests.get(ASHBY_URL.format(slug=slug), headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        data = r.json()
        jobs = []
        for j in data.get('jobs', []):
            jobs.append({
                'title': j.get('title', ''),
                'location': j.get('location', ''),
                'url': j.get('jobUrl', '') or j.get('applyUrl', ''),
                'dept': j.get('department', ''),
                'board': 'ashby',
                'board_slug': slug,
            })
        return jobs
    except Exception:
        return None


def _is_relevant(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in MATCH_KEYWORDS)


def fetch_company_jobs(domain: str, name: str) -> list[dict]:
    """Try all ATSs for one company; return relevant jobs."""
    all_jobs = []
    for slug in _board_slugs(domain, name):
        for probe in (_probe_greenhouse, _probe_lever, _probe_ashby):
            try:
                jobs = probe(slug)
            except Exception:
                jobs = None
            if jobs:
                all_jobs.extend(jobs)
                break  # one ATS found — don't double-count other aliases
        if all_jobs:
            break
    # dedupe by URL + keep only relevant roles
    seen = set()
    relevant = []
    for j in all_jobs:
        url = j.get('url') or j.get('title')
        if url in seen:
            continue
        seen.add(url)
        if _is_relevant(j.get('title', '')):
            relevant.append(j)
    return relevant


def run(conn: sqlite3.Connection, cfg: dict[str, Any]) -> dict[str, int]:
    """Probe qualified companies (small/medium first); store open jobs in `open_jobs`.

    Order: headcount ASC (small companies first — less competition, faster hiring),
    NULL headcount last. Keeps BOTH remote and office jobs — we apply everywhere.
    """
    cur = conn.execute("""
        SELECT id, name, domain, headcount FROM companies
        WHERE qualified = 1
          AND domain IS NOT NULL AND domain != ''
          AND NOT EXISTS (SELECT 1 FROM open_jobs WHERE open_jobs.company_id = companies.id)
        ORDER BY CASE WHEN headcount IS NULL OR headcount = 0 THEN 1 ELSE 0 END,
                 headcount ASC,
                 remote_score DESC
        LIMIT ?
    """, (DAILY_LIMIT,))
    companies = cur.fetchall()

    total_jobs = 0
    found_boards = 0
    for company_id, name, domain, _headcount in companies:
        jobs = fetch_company_jobs(domain, name)
        if jobs:
            found_boards += 1
        for j in jobs:
            conn.execute("""
                INSERT OR IGNORE INTO open_jobs
                    (company_id, title, location, url, dept, board, board_slug)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                company_id, j['title'], j.get('location', ''),
                j.get('url', ''), j.get('dept', ''), j.get('board', ''),
                j.get('board_slug', ''),
            ))
            total_jobs += 1
        # small delay to be polite
        import time
        time.sleep(0.3)

    conn.commit()
    logger.info(
        'OpenJobs: %d companies probed, %d with live boards, %d relevant jobs stored',
        len(companies), found_boards, total_jobs,
    )
    return {'companies_probed': len(companies), 'boards_found': found_boards, 'jobs_stored': total_jobs}
