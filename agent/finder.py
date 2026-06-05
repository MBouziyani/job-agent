import logging
import os
import time

import requests

from db import get_qualified_without_contact, insert_contact

logger = logging.getLogger(__name__)

HUNTER_API = 'https://api.hunter.io/v2/domain-search'

# Ordered highest to lowest priority — first matching tier wins.
_ROLE_TIERS = [
    ['cto', 'chief technology officer', 'chief technical officer'],
    ['co-founder', 'cofounder', 'co founder'],
    [
        'head of engineering', 'vp of engineering', 'vp engineering',
        'director of engineering', 'engineering director',
    ],
    ['engineering manager', 'technical manager', 'tech manager'],
    ['tech lead', 'technical lead', 'lead engineer', 'lead developer', 'staff engineer'],
    ['developer', 'software engineer', 'software developer'],
]


def _tier(position: str) -> int:
    """Return priority tier for a job title (lower = higher priority)."""
    if not position:
        return len(_ROLE_TIERS)
    p = position.lower()
    for i, terms in enumerate(_ROLE_TIERS):
        if any(t in p for t in terms):
            return i
    return len(_ROLE_TIERS)


def _best_contact(emails: list) -> dict | None:
    candidates = [e for e in emails if e.get('value')]
    if not candidates:
        return None
    # Prefer personal addresses; within same tier sort by confidence desc.
    candidates.sort(key=lambda e: (
        0 if e.get('type') == 'personal' else 1,
        _tier(e.get('position', '')),
        -(e.get('confidence') or 0),
    ))
    return candidates[0]


def _search_domain(domain: str, api_key: str) -> list:
    try:
        resp = requests.get(
            HUNTER_API,
            params={'domain': domain, 'api_key': api_key, 'limit': 10},
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json().get('data', {}).get('emails', [])
        logger.warning('Hunter.io %s → HTTP %d', domain, resp.status_code)
    except requests.RequestException as exc:
        logger.error('Hunter.io request failed for %s: %s', domain, exc)
    return []


def run(conn, cfg: dict) -> dict:
    api_key = os.environ.get('HUNTER_API_KEY')
    if not api_key:
        logger.error('HUNTER_API_KEY not set — skipping finder')
        return {'processed': 0, 'found': 0, 'skipped': 0}

    companies = get_qualified_without_contact(conn)
    logger.info(
        'Finder: get_qualified_without_contact returned %d companies (qualified=1, domain set, no contact yet)',
        len(companies),
    )

    processed = found = skipped = 0

    for company in companies:
        domain = company['domain']
        emails = _search_domain(domain, api_key)
        processed += 1

        contact = _best_contact(emails)
        if not contact:
            logger.debug('No usable contact for %s (%s)', company['name'], domain)
            skipped += 1
            time.sleep(1)
            continue

        first    = (contact.get('first_name') or '').strip()
        last     = (contact.get('last_name')  or '').strip()
        name     = f'{first} {last}'.strip() or None
        role     = contact.get('position') or None
        email    = contact['value']
        verified = (contact.get('confidence') or 0) > 70

        insert_contact(conn, company['id'], name, role, email, verified)
        found += 1
        logger.info(
            'CONTACT  %-30s → %-40s role=%-30s confidence=%d',
            company['name'], email, role or '—', contact.get('confidence') or 0,
        )

        time.sleep(1)  # Hunter.io free plan: 25 req/month; keep a polite pace

    logger.info(
        'Finder done — processed=%d found=%d skipped=%d',
        processed, found, skipped,
    )
    return {'processed': processed, 'found': found, 'skipped': skipped}
