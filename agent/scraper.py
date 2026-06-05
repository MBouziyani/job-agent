import logging
import re
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from db import company_exists, insert_company

logger = logging.getLogger(__name__)

REMOTEOK_API = 'https://remoteok.com/api'
HIMALAYAS_COMPANIES = 'https://himalayas.app/companies'

_REMOTEOK_HEADERS = {
    'User-Agent': 'Mozilla/5.0',
    'Accept': 'application/json',
}

_HIMALAYAS_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/125.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://himalayas.app/',
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
        resp = requests.get(
            REMOTEOK_API,
            headers=_REMOTEOK_HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        jobs = resp.json()
    except Exception as exc:
        logger.error('RemoteOK request failed: %s', exc)
        return 0

    # First element is a legal notice dict — skip it
    if jobs and isinstance(jobs[0], dict) and 'legal' in jobs[0]:
        jobs = jobs[1:]

    seen_domains: set[str] = set()
    new_count = 0

    for job in jobs:
        if not isinstance(job, dict):
            continue

        company_url = job.get('company_url') or ''
        domain = extract_domain(company_url)
        if not domain or domain in seen_domains:
            continue
        seen_domains.add(domain)

        if company_exists(conn, domain):
            continue

        name = (job.get('company') or '').strip()
        if not name:
            continue

        tags = job.get('tags') or []
        stack = ','.join(str(t) for t in tags)

        raw_desc = job.get('description') or ''
        description = _strip_html(raw_desc)[:500]

        company_data = {
            'name': name,
            'domain': domain,
            'website': company_url,
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
            logger.debug('Added %s (%s)', name, domain)
        except Exception as exc:
            logger.error('Insert failed for %s: %s', domain, exc)

    logger.info('RemoteOK: added %d new companies', new_count)
    return new_count


def _parse_headcount(text: str) -> int | None:
    text = text.replace(',', '')
    match = re.search(r'(\d+)', text)
    return int(match.group(1)) if match else None


def scrape_himalayas(conn, cfg: dict) -> int:
    logger.info('Scraping Himalayas...')
    new_count = 0
    page = 1

    while page <= 50:
        url = f'{HIMALAYAS_COMPANIES}?page={page}'
        try:
            resp = requests.get(url, headers=_HIMALAYAS_HEADERS, timeout=30)
            resp.raise_for_status()
        except Exception as exc:
            logger.error('Himalayas page %d failed: %s', page, exc)
            break

        soup = BeautifulSoup(resp.text, 'lxml')

        # Himalayas renders company cards — try selectors in order of specificity
        cards = (
            soup.select('li[data-company-slug]')
            or soup.select('div[data-company-slug]')
            or soup.select('ul.companies li')
            or soup.select('div.companies > div')
            or soup.select('article')
        )

        if not cards:
            logger.info('Himalayas page %d: no cards found, stopping', page)
            break

        for card in cards:
            # Name — prefer heading elements, fall back to named class
            name_el = (
                card.select_one('h2')
                or card.select_one('h3')
                or card.select_one('[class*="name"]')
            )
            name = name_el.get_text(strip=True) if name_el else ''
            if not name:
                continue

            # Website — prefer external href, fall back to Himalayas profile URL
            website_href = ''
            ext_link = card.select_one('a[href^="http"]')
            int_link = card.select_one('a[href^="/companies/"]')
            if ext_link:
                website_href = ext_link.get('href', '')
            elif int_link:
                website_href = 'https://himalayas.app' + int_link.get('href', '')

            domain = extract_domain(website_href) if website_href else None

            # Last resort: synthesise a domain from the data-slug attribute
            if not domain:
                slug = card.get('data-company-slug', '')
                domain = f'{slug}.himalayas' if slug else None

            if not domain:
                continue

            if company_exists(conn, domain):
                continue

            # Description / tagline
            desc_el = (
                card.select_one('p')
                or card.select_one('[class*="tagline"]')
                or card.select_one('[class*="description"]')
            )
            description = desc_el.get_text(strip=True)[:500] if desc_el else ''

            # Team size — scan all short text nodes for "employee" / "people" keywords
            headcount = None
            for el in card.select('span, li, div, p'):
                txt = el.get_text(strip=True)
                if len(txt) < 60 and ('employee' in txt.lower() or 'people' in txt.lower()):
                    headcount = _parse_headcount(txt)
                    if headcount:
                        break

            company_data = {
                'name': name,
                'domain': domain,
                'website': website_href,
                'headcount': headcount,
                'countries_count': None,
                'stack': '',
                'description': description,
                'source': 'himalayas',
                'remote_score': 0,
            }

            try:
                insert_company(conn, company_data)
                new_count += 1
                logger.debug('Added %s (%s)', name, domain)
            except Exception as exc:
                logger.error('Insert failed for %s: %s', domain, exc)

        # Stop when no next-page link exists or this page had very few results
        next_link = (
            soup.select_one('a[rel="next"]')
            or soup.select_one('a[aria-label="Next"]')
            or soup.select_one('a[aria-label="next"]')
        )
        if not next_link and len(cards) < 10:
            break

        page += 1
        time.sleep(1)  # be polite between pages

    logger.info('Himalayas: added %d new companies', new_count)
    return new_count


def run_all(conn, cfg: dict) -> dict[str, int]:
    sources = cfg.get('scraping', {}).get('sources', {})
    results: dict[str, int] = {}

    if sources.get('remoteok', True):
        results['remoteok'] = scrape_remoteok(conn, cfg)

    if sources.get('himalayas', True):
        results['himalayas'] = scrape_himalayas(conn, cfg)

    return results
