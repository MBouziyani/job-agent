import logging
import time

import requests
from bs4 import BeautifulSoup

from db import company_exists, insert_company, update_company_domain
from scraper_himalayas import scrape_himalayas

REMOTIVE_API = 'https://remotive.com/api/remote-jobs'
JOBSPRESSO_URL = 'https://jobspresso.co/remote-work/'
WELLFOUND_URL = 'https://wellfound.com/jobs?remote=true'

logger = logging.getLogger(__name__)

REMOTEOK_API = 'https://remoteok.com/api'
WWR_RSS = 'https://weworkremotely.com/remote-jobs.rss'
CLEARBIT_SUGGEST = 'https://autocomplete.clearbit.com/v1/companies/suggest'

_REMOTEOK_HEADERS = {
    'User-Agent': 'Mozilla/5.0',
    'Accept': 'application/json',
}


def _strip_html(raw: str) -> str:
    return BeautifulSoup(raw, 'lxml').get_text(' ', strip=True)


def _fix_encoding(s: str) -> str:
    """Fix UTF-8 bytes that were decoded as latin-1 (e.g. 'NestlÃ©' → 'Nestlé')."""
    try:
        return s.encode('latin-1').decode('utf-8')
    except (UnicodeDecodeError, UnicodeEncodeError):
        return s


def _clearbit_domain(name: str) -> str | None:
    """Clearbit Autocomplete lookup — free, no API key needed."""
    try:
        resp = requests.get(
            CLEARBIT_SUGGEST,
            params={'query': name},
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json()
        if results:
            return results[0].get('domain') or None
    except Exception as exc:
        logger.debug('Clearbit lookup failed for %s: %s', name, exc)
    return None


def scrape_remoteok(conn, cfg: dict) -> int:
    logger.info('Scraping RemoteOK...')
    try:
        resp = requests.get(REMOTEOK_API, headers=_REMOTEOK_HEADERS, timeout=30)
        resp.raise_for_status()
        resp.encoding = 'utf-8'  # override wrong Content-Type charset before .json()
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
        name = _fix_encoding(job['company'].strip())
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
            'domain': None,
            'website': job.get('apply_url') or job['url'],
            'headcount': None,
            'countries_count': None,
            'stack': stack,
            'description': description,
            'job_title': job.get('position', ''),
            'source': 'remoteok',
            'remote_score': 0,
        }

        try:
            row_id = insert_company(conn, company_data)
            new_count += 1
            domain = _clearbit_domain(name)
            if domain:
                update_company_domain(conn, row_id, domain)
            logger.debug('Added %s%s', name, f' → {domain}' if domain else '')
            time.sleep(0.2)
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
        name = _fix_encoding(title.rsplit(' at ', 1)[-1].strip())
        if not name:
            continue

        # Extract job title from before ' at '
        before_at = title.rsplit(' at ', 1)[0].strip()
        if ': ' in before_at:
            job_title = before_at.split(': ', 1)[-1].strip()
        else:
            job_title = before_at

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
            'domain': None,
            'website': website,
            'headcount': None,
            'countries_count': None,
            'stack': '',
            'description': description,
            'job_title': job_title,
            'source': 'weworkremotely',
            'remote_score': 0,
        }

        try:
            row_id = insert_company(conn, company_data)
            new_count += 1
            domain = _clearbit_domain(name)
            if domain:
                update_company_domain(conn, row_id, domain)
            logger.debug('Added %s%s', name, f' → {domain}' if domain else '')
            time.sleep(0.2)
        except Exception as exc:
            logger.error('Insert failed for %s: %s', name, exc)

    logger.info('We Work Remotely: added %d new companies', new_count)
    return new_count


def scrape_remotive(conn, cfg: dict) -> int:
    logger.info('Scraping Remotive API...')
    try:
        resp = requests.get(
            REMOTIVE_API,
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=30,
        )
        resp.raise_for_status()
        jobs = resp.json().get('jobs', [])
    except Exception as exc:
        logger.error('Remotive request failed: %s', exc)
        return 0

    logger.info('Remotive raw records: %d', len(jobs))

    seen: set[str] = set()
    new_count = 0

    for job in jobs:
        name = _fix_encoding((job.get('company_name') or '').strip())
        if not name or name in seen:
            continue
        seen.add(name)

        if company_exists(conn, name, 'remotive'):
            continue

        tags = job.get('tags') or []
        stack = ','.join(str(t) for t in tags)
        raw_desc = job.get('description') or ''
        description = _strip_html(raw_desc)[:500]
        website = job.get('company_url') or job.get('url') or ''

        company_data = {
            'name': name,
            'domain': None,
            'website': website,
            'headcount': None,
            'countries_count': None,
            'stack': stack,
            'description': description,
            'job_title': job.get('title', ''),
            'source': 'remotive',
            'remote_score': 0,
        }

        try:
            row_id = insert_company(conn, company_data)
            new_count += 1
            domain = _clearbit_domain(name)
            if domain:
                update_company_domain(conn, row_id, domain)
            logger.debug('Added %s%s', name, f' → {domain}' if domain else '')
            time.sleep(0.2)
        except Exception as exc:
            logger.error('Insert failed for %s: %s', name, exc)

    logger.info('Remotive: added %d new companies', new_count)
    return new_count


def scrape_jobspresso(conn, cfg: dict) -> int:
    logger.info('Scraping Jobspresso...')
    try:
        resp = requests.get(
            JOBSPRESSO_URL,
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=30,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.error('Jobspresso request failed: %s', exc)
        return 0

    soup = BeautifulSoup(resp.content, 'lxml')

    # WP Job Manager structure
    listings = soup.select('li.job_listing')
    if not listings:
        listings = soup.select('[class*="job_listing"]')
    logger.info('Jobspresso raw listings: %d', len(listings))

    seen: set[str] = set()
    new_count = 0

    for listing in listings:
        company_el = listing.select_one('.company strong') or listing.select_one('.company')
        if not company_el:
            continue
        name = _fix_encoding(company_el.get_text(strip=True))
        if not name or name in seen:
            continue
        seen.add(name)

        if company_exists(conn, name, 'jobspresso'):
            continue

        link_el = listing.select_one('a')
        website = link_el['href'] if link_el and link_el.get('href') else ''

        title_el = listing.select_one('h3') or listing.select_one('.position')
        job_title = title_el.get_text(strip=True) if title_el else ''

        # Try to find a proper job description (not just the title)
        desc_el = (
            listing.select_one('.job_listing-description')
            or listing.select_one('.listing-description')
            or listing.select_one('.description p')
            or listing.select_one('p.description')
        )
        description = _strip_html(desc_el.get_text(strip=True))[:500] if desc_el else job_title

        company_data = {
            'name': name,
            'domain': None,
            'website': website,
            'headcount': None,
            'countries_count': None,
            'stack': '',
            'description': description,
            'job_title': job_title,
            'source': 'jobspresso',
            'remote_score': 0,
        }

        try:
            row_id = insert_company(conn, company_data)
            new_count += 1
            domain = _clearbit_domain(name)
            if domain:
                update_company_domain(conn, row_id, domain)
            logger.debug('Added %s%s', name, f' → {domain}' if domain else '')
            time.sleep(0.2)
        except Exception as exc:
            logger.error('Insert failed for %s: %s', name, exc)

    logger.info('Jobspresso: added %d new companies', new_count)
    return new_count


def scrape_wellfound(conn, cfg: dict) -> int:
    logger.info('Scraping Wellfound (Playwright)...')
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error('Playwright not installed — skipping Wellfound')
        return 0

    names: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(user_agent='Mozilla/5.0')
            page.goto(WELLFOUND_URL, wait_until='load', timeout=60000)

            # Scroll 3 times to load more listings
            for _ in range(3):
                page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                page.wait_for_timeout(2000)

            # Company links are /company/<slug>; extract unique slugs → display names
            anchors = page.query_selector_all('a[href*="/company/"]')
            seen_hrefs: set[str] = set()
            for a in anchors:
                href = a.get_attribute('href') or ''
                if href in seen_hrefs:
                    continue
                seen_hrefs.add(href)
                text = (a.inner_text() or '').strip()
                if text and len(text) > 1:
                    names.append(text)

            browser.close()
    except Exception as exc:
        logger.error('Wellfound Playwright error: %s', exc)
        return 0

    logger.info('Wellfound raw company names: %d', len(names))

    seen: set[str] = set()
    new_count = 0

    for raw_name in names:
        name = _fix_encoding(raw_name)
        if not name or name in seen:
            continue
        seen.add(name)

        if company_exists(conn, name, 'wellfound'):
            continue

        company_data = {
            'name': name,
            'domain': None,
            'website': '',
            'headcount': None,
            'countries_count': None,
            'stack': '',
            'description': '',
            'job_title': '',
            'source': 'wellfound',
            'remote_score': 0,
        }

        try:
            row_id = insert_company(conn, company_data)
            new_count += 1
            domain = _clearbit_domain(name)
            if domain:
                update_company_domain(conn, row_id, domain)
            logger.debug('Added %s%s', name, f' → {domain}' if domain else '')
            time.sleep(0.2)
        except Exception as exc:
            logger.error('Insert failed for %s: %s', name, exc)

    logger.info('Wellfound: added %d new companies', new_count)
    return new_count


SO_JOBS_FEED = 'https://stackoverflow.com/jobs/feed'


def scrape_stackoverflow(conn, cfg: dict) -> int:
    logger.info('Scraping Stack Overflow Jobs RSS...')
    try:
        resp = requests.get(
            SO_JOBS_FEED,
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=30,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.error('Stack Overflow request failed: %s', exc)
        return 0

    soup = BeautifulSoup(resp.content, 'xml')
    items = soup.find_all('item')
    logger.info('Stack Overflow raw records: %d', len(items))

    seen: set[str] = set()
    new_count = 0

    for item in items:
        # Title format: "Job Title at Company Name"
        title_el = item.find('title')
        title = title_el.get_text(strip=True) if title_el else ''
        if ' at ' not in title:
            continue
        name = _fix_encoding(title.rsplit(' at ', 1)[-1].strip())
        if not name:
            continue

        job_title = title.rsplit(' at ', 1)[0].strip()

        # Some SO titles have the company in a <company> element; prefer that if available
        company_el = item.find('company')
        if company_el:
            parsed_name = company_el.get_text(strip=True)
            if parsed_name:
                name = _fix_encoding(parsed_name)

        if name in seen:
            continue
        seen.add(name)

        if company_exists(conn, name, 'stackoverflow'):
            continue

        link_el = item.find('link')
        website = link_el.get_text(strip=True) if link_el else ''

        desc_el = item.find('description')
        raw_desc = desc_el.get_text(strip=True) if desc_el else ''
        description = _strip_html(raw_desc)[:500] if raw_desc else ''

        company_data = {
            'name': name,
            'domain': None,
            'website': website,
            'headcount': None,
            'countries_count': None,
            'stack': '',
            'description': description,
            'job_title': job_title,
            'source': 'stackoverflow',
            'remote_score': 0,
        }

        try:
            row_id = insert_company(conn, company_data)
            new_count += 1
            domain = _clearbit_domain(name)
            if domain:
                update_company_domain(conn, row_id, domain)
            logger.debug('Added %s%s', name, f' → {domain}' if domain else '')
            time.sleep(0.2)
        except Exception as exc:
            logger.error('Insert failed for %s: %s', name, exc)

    logger.info('Stack Overflow Jobs: added %d new companies', new_count)
    return new_count


HN_ALGOLIA_API = 'https://hn.algolia.com/api/v1'


def scrape_hackernews(conn, cfg: dict) -> int:
    logger.info('Scraping HN Who Is Hiring...')
    try:
        # Search for the most recent "Who Is Hiring" story
        resp = requests.get(
            f'{HN_ALGOLIA_API}/search',
            params={
                'query': 'Who Is Hiring',
                'tags': 'story',
                'hitsPerPage': 1,
                'numericFilters': 'created_at_i>' + str(int(time.time()) - 45*86400),
            },
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error('HN Algolia search request failed: %s', exc)
        return 0

    hits = data.get('hits', [])
    if not hits:
        logger.warning('HN Algolia: no "Who Is Hiring" stories found')
        return 0

    story = hits[0]
    story_id = story.get('objectID') or story.get('story_id')
    logger.info('HN Who Is Hiring story ID: %s — "%s"', story_id, story.get('title', ''))

    if not story_id:
        logger.warning('HN Algolia: no story ID found')
        return 0

    # Fetch comments for this story
    try:
        resp = requests.get(
            f'{HN_ALGOLIA_API}/items/{story_id}',
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=30,
        )
        resp.raise_for_status()
        story_data = resp.json()
    except Exception as exc:
        logger.error('HN Algolia item request failed: %s', exc)
        return 0

    children = story_data.get('children', [])
    logger.info('HN Who Is Hiring: %d comments to scan', len(children))

    seen: set[str] = set()
    new_count = 0

    for comment in children:
        text = (comment.get('text') or '').strip()
        if not text:
            continue

        # The first line/sentence typically has the company name
        # Common formats:
        #   "Company Name | Job Title | Location | ..."
        #   "Company Name (City) | ..."
        #   "Company Name is hiring..."
        #   "Company Name — Job Title — ..."
        first_line = text.split('\n')[0].strip()

        # Strip common prefixes like "| " or leading bullet
        first_line = first_line.lstrip('|').lstrip('-').lstrip('*').strip()

        # Try to extract company name: take everything before first pipe, em dash, or "is hiring"
        # Format 1: "Company | Title | Location"
        if ' | ' in first_line:
            possible_name = first_line.split(' | ')[0].strip()
        elif ' — ' in first_line:
            possible_name = first_line.split(' — ')[0].strip()
        elif ' – ' in first_line:
            possible_name = first_line.split(' – ')[0].strip()
        elif ' is hiring' in first_line.lower():
            possible_name = first_line.split(' is hiring')[0].strip()
        elif ' is looking' in first_line.lower():
            possible_name = first_line.split(' is looking')[0].strip()
        elif ' has an opening' in first_line.lower():
            possible_name = first_line.split(' has an opening')[0].strip()
        else:
            # Fallback: take everything before first sentence period, or first ~50 chars
            possible_name = first_line.split('. ')[0].strip() if '. ' in first_line else first_line[:50].strip()

        # Clean up: remove parenthetical locations, trailing punctuation
        possible_name = possible_name.rstrip(',;:.')
        # Remove parenthetical like "(Remote)" or "(US only)"
        possible_name = possible_name.split(' (')[0].strip()

        # Filter: company names should be at least 2 chars, not contain HTML tags
        if len(possible_name) < 2 or '<' in possible_name:
            continue

        name = _fix_encoding(possible_name)

        if name in seen:
            continue
        seen.add(name)

        if company_exists(conn, name, 'hackernews'):
            continue

        # Try to extract job title from the first line as well
        job_title = ''
        if ' | ' in first_line:
            parts = first_line.split(' | ')
            if len(parts) >= 2:
                job_title = parts[1].strip()
        elif ' — ' in first_line:
            parts = first_line.split(' — ')
            if len(parts) >= 2:
                job_title = parts[1].strip()
        elif ' – ' in first_line:
            parts = first_line.split(' – ')
            if len(parts) >= 2:
                job_title = parts[1].strip()

        # Description: the full comment text (stripped of HTML)
        description = _strip_html(text)[:500]

        company_data = {
            'name': name,
            'domain': None,
            'website': '',
            'headcount': None,
            'countries_count': None,
            'stack': '',
            'description': description,
            'job_title': job_title,
            'source': 'hackernews',
            'remote_score': 0,
        }

        try:
            row_id = insert_company(conn, company_data)
            new_count += 1
            domain = _clearbit_domain(name)
            if domain:
                update_company_domain(conn, row_id, domain)
            logger.debug('Added %s%s', name, f' → {domain}' if domain else '')
            time.sleep(0.2)
        except Exception as exc:
            logger.error('Insert failed for %s: %s', name, exc)

    logger.info('HN Who Is Hiring: added %d new companies', new_count)
    return new_count


def run_all(conn, cfg: dict) -> dict[str, int]:
    sources = cfg.get('scraping', {}).get('sources', {})
    results: dict[str, int] = {}

    if sources.get('remoteok', True):
        results['remoteok'] = scrape_remoteok(conn, cfg)

    if sources.get('we_work_remotely', True):
        results['we_work_remotely'] = scrape_weworkremotely(conn, cfg)

    if sources.get('remotive', True):
        results['remotive'] = scrape_remotive(conn, cfg)

    if sources.get('jobspresso', True):
        results['jobspresso'] = scrape_jobspresso(conn, cfg)

    if sources.get('wellfound', False):
        results['wellfound'] = scrape_wellfound(conn, cfg)

    if sources.get('stackoverflow', True):
        results['stackoverflow'] = scrape_stackoverflow(conn, cfg)

    if sources.get('hackernews', True):
        results['hackernews'] = scrape_hackernews(conn, cfg)

    if sources.get('himalayas', True):
        results['himalayas'] = scrape_himalayas(conn, cfg)

    return results
