import logging
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from db import company_exists, insert_company

logger = logging.getLogger(__name__)

REMOTEOK_API = 'https://remoteok.com/api'
WWR_RSS = 'https://weworkremotely.com/remote-jobs.rss'

_REMOTEOK_HEADERS = {
    'User-Agent': 'Mozilla/5.0',
    'Accept': 'application/json',
}


def extract_domain(url: str) -> str | None:
    if not url:
        return None
    try:
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        host = urlparse(url).netloc.lower()
        if host.startswith('www.'):
            host = host[4:]
        return host or None
    except Exception:
        return None


def _strip_html(raw: str) -> str:
    return BeautifulSoup(raw, 'lxml').get_text(' ', strip=True)


def scrape_remoteok(conn, cfg: dict) -> int:
    logger.info('Scraping RemoteOK...')
    try:
        resp = requests.get(REMOTEOK_API, headers=_REMOTEOK_HEADERS, timeout=30)
        resp.raise_for_status()
        resp.encoding = 'utf-8'  # API serves UTF-8; override any wrong Content-Type charset
        jobs = resp.json()
    except Exception as exc:
        logger.error('RemoteOK request failed: %s', exc)
        return 0

    # Record 0 is a legal notice (no 'company' key) — filter it and any incomplete entries
    jobs = [j for j in jobs if isinstance(j, dict) and j.get('company') and j.get('url')]

    # Deduplicate by company name within this batch
    seen: set[str] = set()
    unique: list[tuple[dict, str]] = []
    for job in jobs:
        name = job['company'].strip()
        if not name or name in seen:
            continue
        seen.add(name)
        unique.append((job, name))

    logger.info('RemoteOK raw records: %d, after dedup: %d', len(jobs), len(unique))

    new_count = 0
    for job, name in unique:
        if company_exists(conn, name, 'remoteok'):
            continue

        tags = job.get('tags') or []
        stack = ','.join(str(t) for t in tags)
        raw_desc = job.get('description') or ''
        description = _strip_html(raw_desc)[:500]

        company_data = {
            'name': name,
            'domain': None,  # finder.py (Session 4) will discover real domain via Hunter.io
            'website': job.get('apply_url') or job['url'],
            'headcount': None,
            'countries_count': None,
            'stack': stack,
            'description': description,
            'source': 'remoteok',
            'remote_score': 0,
        }

        try:
            insert_company(conn, company_data)
            new_count += 1
            logger.debug('Added %s', name)
        except Exception as exc:
            logger.error('Insert failed for %s: %s', name, exc)

    logger.info('RemoteOK: added %d new companies', new_count)
    return new_count


def scrape_weworkremotely(conn, cfg: dict) -> int:
    logger.info('Scraping We Work Remotely RSS...')
    try:
        resp = requests.get(
            WWR_RSS,
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=30,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.error('We Work Remotely request failed: %s', exc)
        return 0

    soup = BeautifulSoup(resp.content, 'xml')
    items = soup.find_all('item')
    logger.info('We Work Remotely raw records: %d', len(items))

    seen: set[str] = set()
    new_count = 0

    for item in items:
        # Title format: "Category: Job Title at Company Name"
        title_el = item.find('title')
        title = title_el.get_text(strip=True) if title_el else ''
        if ' at ' not in title:
            continue
        name = title.rsplit(' at ', 1)[-1].strip()
        if not name:
            continue

        if name in seen:
            continue
        seen.add(name)

        if company_exists(conn, name, 'weworkremotely'):
            continue

        link_el = item.find('link')
        website = link_el.get_text(strip=True) if link_el else ''

        desc_el = item.find('description')
        raw_desc = desc_el.get_text(strip=True) if desc_el else ''
        description = _strip_html(raw_desc)[:500] if raw_desc else ''

        company_data = {
            'name': name,
            'domain': None,  # finder.py (Session 4) will discover real domain via Hunter.io
            'website': website,
            'headcount': None,
            'countries_count': None,
            'stack': '',
            'description': description,
            'source': 'weworkremotely',
            'remote_score': 0,
        }

        try:
            insert_company(conn, company_data)
            new_count += 1
            logger.debug('Added %s', name)
        except Exception as exc:
            logger.error('Insert failed for %s: %s', name, exc)

    logger.info('We Work Remotely: added %d new companies', new_count)
    return new_count


def run_all(conn, cfg: dict) -> dict[str, int]:
    sources = cfg.get('scraping', {}).get('sources', {})
    results: dict[str, int] = {}

    if sources.get('remoteok', True):
        results['remoteok'] = scrape_remoteok(conn, cfg)

    if sources.get('we_work_remotely', True):
        results['we_work_remotely'] = scrape_weworkremotely(conn, cfg)

    return results
