"""
Himalayas.app Job Scraper
API: https://himalayas.app/jobs/api
Free, no API key needed. Returns up to 104K+ remote jobs.
"""
import logging
import re
import time
import requests
from db import company_exists, insert_company, update_company_domain

logger = logging.getLogger(__name__)

HIMALAYAS_API = 'https://himalayas.app/jobs/api'
HIMALAYAS_LIMIT = 20
HIMALAYAS_MAX_TOTAL = 60
HIMALAYAS_MAX_PAGES = 50  # Stop after 50 pages (1000 companies) to avoid infinite loops

# Only insert companies with dev/engineering parent categories or job titles
DEV_KEYWORDS = re.compile(
    r'(developer|engineer|engine|software|full.?stack|backend|frontend|front.?end|'
    r'devops|sre|infrastructure|data.?engineer|ml.?engineer|ai.?engineer|'
    r'backend|fullstack|technical|tech.?lead|architect|programmer|coding|'
    r'systems.?engineer|platform.?engineer|site.?reliability|security.?engineer)',
    re.IGNORECASE,
)


def _strip_html(raw: str) -> str:
    from bs4 import BeautifulSoup
    return BeautifulSoup(raw, 'lxml').get_text(' ', strip=True)


def _fix_encoding(s: str) -> str:
    try:
        return s.encode('latin-1').decode('utf-8')
    except (UnicodeDecodeError, UnicodeEncodeError):
        return s


def _clearbit_domain(name: str) -> str | None:
    import requests as req
    try:
        resp = req.get(
            'https://autocomplete.clearbit.com/v1/companies/suggest',
            params={'query': name},
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json()
        if results:
            return results[0].get('domain') or None
    except Exception:
        pass
    return None


def scrape_himalayas(conn, cfg: dict) -> int:
    logger.info('Scraping Himalayas API...')

    # Get the last fetched item count to resume
    offset = 0
    new_count = 0
    seen: set[str] = set()
    page_count = 0

    while new_count < HIMALAYAS_MAX_TOTAL and page_count < HIMALAYAS_MAX_PAGES:
        page_count += 1
        try:
            resp = requests.get(
                HIMALAYAS_API,
                params={'limit': HIMALAYAS_LIMIT, 'offset': offset},
                headers={'User-Agent': 'Mozilla/5.0'},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error('Himalayas request failed at offset %d: %s', offset, exc)
            break

        jobs = data.get('jobs', [])
        if not jobs:
            logger.info('Himalayas: no more jobs at offset %d', offset)
            break

        for job in jobs:
            # Extract company name
            raw_name = (job.get('companyName') or '').strip()
            if not raw_name:
                continue

            name = _fix_encoding(raw_name)

            # Deduplicate in this batch
            name_lower = name.lower()
            if name_lower in seen:
                continue
            seen.add(name_lower)

            # Check if already in DB
            if company_exists(conn, name, 'himalayas'):
                continue

            # Build job title
            job_title = (job.get('title') or '').strip()

            # SKIP non-dev roles — check parent categories and job title
            parent_cats = [str(pc).lower() for pc in (job.get('parentCategories') or [])]
            is_dev = (
                'developer' in parent_cats
                or DEV_KEYWORDS.search(job_title)
            )
            if not is_dev:
                continue

            # Build company data
            categories = job.get('categories', []) or []
            stack = ','.join(str(c) for c in categories)

            # Build description
            description = ''
            excerpt = (job.get('excerpt') or '').strip()
            raw_desc = (job.get('description') or '').strip()
            if excerpt:
                description = _strip_html(excerpt)[:500]
            elif raw_desc:
                description = _strip_html(raw_desc)[:500]

            # Location info
            locations = job.get('locationRestrictions', []) or []
            location_str = ', '.join(str(l) for l in locations) if locations else ''

            apply_url = (job.get('applicationLink') or '').strip()

            company_data = {
                'name': name,
                'domain': None,
                'website': apply_url or '',
                'headcount': None,
                'countries_count': len(locations) if locations else None,
                'stack': stack,
                'description': f"{job_title} | {location_str} | {description}"[:500],
                'job_title': job_title,
                'source': 'himalayas',
                'remote_score': 0,
            }

            try:
                row_id = insert_company(conn, company_data)
                new_count += 1
                # Clearbit lookup — fire-and-forget (don't block on it)
                try:
                    domain = _clearbit_domain(name)
                    if domain:
                        update_company_domain(conn, row_id, domain)
                except Exception:
                    pass
                logger.debug('Added %s — %s', name, job_title[:40])
                time.sleep(0.02)
            except Exception as exc:
                logger.error('Insert failed for %s: %s', name, exc)

        logger.info('Himalayas: offset=%d, page_jobs=%d, new_this_run=%d',
                     offset, len(jobs), new_count)
        offset += HIMALAYAS_LIMIT

        # Minimal rate limiting between pages
        time.sleep(0.3)

    logger.info('Himalayas: added %d new companies total', new_count)
    return new_count
