import logging
import os
import time

import requests

from db import get_qualified_without_contact, insert_contact

logger = logging.getLogger(__name__)

HUNTER_API = 'https://api.hunter.io/v2/domain-search'
APOLLO_API = 'https://api.apollo.io/api/v1/people/search'

# Headcount-aware role tiers — first matching tier wins (lower index = higher priority).
_TIERS_SMALL = [
    ['cto', 'chief technology officer', 'chief technical officer'],
    ['co-founder', 'cofounder', 'co founder'],
    ['head of engineering', 'vp of engineering', 'vp engineering',
     'director of engineering', 'engineering director'],
]

_TIERS_MID = [
    ['engineering manager', 'technical manager', 'tech manager'],
    ['tech lead', 'technical lead', 'lead engineer', 'lead developer', 'staff engineer'],
    ['technical recruiter', 'tech recruiter'],
    ['cto', 'chief technology officer', 'chief technical officer'],
]

_TIERS_LARGE = [
    ['technical recruiter', 'tech recruiter'],
    ['talent acquisition', 'talent partner', 'talent sourcer', 'talent specialist'],
    ['people operations', 'people ops', 'hr manager', 'human resources'],
    ['engineering manager', 'technical manager', 'tech manager'],
]


def _tiers_for(headcount: int | None) -> list:
    if headcount is not None and headcount < 30:
        return _TIERS_SMALL
    if headcount is not None and headcount <= 80:
        return _TIERS_MID
    return _TIERS_LARGE


def _tier(position: str, headcount: int | None) -> int:
    tiers = _tiers_for(headcount)
    if not position:
        return len(tiers)
    p = position.lower()
    for i, terms in enumerate(tiers):
        if any(t in p for t in terms):
            return i
    return len(tiers)


def _best_contact(emails: list, headcount: int | None) -> dict | None:
    candidates = [e for e in emails if e.get('value')]
    if not candidates:
        return None
    candidates.sort(key=lambda e: (
        0 if e.get('type') == 'personal' else 1,
        _tier(e.get('position', ''), headcount),
        -(e.get('confidence') or 0),
    ))
    return candidates[0]


def _hunter_search(domain: str, api_key: str) -> list:
    """Search Hunter.io for contacts at a domain. Returns list of email dicts."""
    try:
        resp = requests.get(
            HUNTER_API,
            params={'domain': domain, 'api_key': api_key, 'limit': 10},
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json().get('data', {}).get('emails', [])
        if resp.status_code == 429:
            logger.warning('Hunter.io 429 rate-limited for %s', domain)
        else:
            logger.warning('Hunter.io %s → HTTP %d', domain, resp.status_code)
    except requests.RequestException as exc:
        logger.error('Hunter.io request failed for %s: %s', domain, exc)
    return []


def _apollo_search(domain: str, api_key: str) -> list:
    """Search Apollo.io for contacts at a domain. Returns list of email dicts."""
    try:
        resp = requests.post(
            APOLLO_API,
            headers={
                'X-Api-Key': api_key,
                'Content-Type': 'application/json',
            },
            json={
                'q_organization_domains': [domain],
                'person_titles': [
                    'cto', 'chief technology officer', 'vp engineering',
                    'engineering manager', 'tech lead', 'head of engineering',
                    'software engineer', 'full stack developer', 'backend engineer',
                    'technical recruiter', 'talent acquisition',
                ],
                'page': 1,
                'per_page': 5,
            },
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            people = data.get('people', []) or data.get('data', {}).get('people', [])
            results = []
            for person in people:
                email = person.get('email') or person.get('work_email', '')
                if not email:
                    continue
                results.append({
                    'value': email,
                    'first_name': person.get('first_name', ''),
                    'last_name': person.get('last_name', ''),
                    'position': person.get('title', ''),
                    'type': 'personal',
                    'confidence': 90 if person.get('email_status', '') == 'verified' else 50,
                })
            return results
        if resp.status_code == 429:
            logger.warning('Apollo.io 429 rate-limited for %s', domain)
        else:
            logger.debug('Apollo.io %s → HTTP %d', domain, resp.status_code)
    except requests.RequestException as exc:
        logger.debug('Apollo.io request failed for %s: %s', domain, exc)
    return []


def run(conn, cfg: dict) -> dict:
    hunter_key = os.environ.get('HUNTER_API_KEY')
    apollo_key = os.environ.get('APOLLO_API_KEY')
    
    if not hunter_key and not apollo_key:
        logger.error('Neither HUNTER_API_KEY nor APOLLO_API_KEY set — skipping finder')
        return {'processed': 0, 'found': 0, 'skipped': 0}

    companies = get_qualified_without_contact(conn)
    logger.info(
        'Finder: %d companies need contacts (Hunter=%s, Apollo=%s)',
        len(companies),
        'yes' if hunter_key else 'no',
        'yes' if apollo_key else 'no',
    )

    processed = found = skipped = hunter_used = apollo_used = 0

    for company in companies:
        domain = company['domain']
        if not domain:
            skipped += 1
            continue

        processed += 1
        emails = []

        # 1. Try Hunter.io first
        if hunter_key:
            emails = _hunter_search(domain, hunter_key)
            hunter_used += 1

        # 2. Fallback to Apollo.io if Hunter found nothing or rate-limited
        if not emails and apollo_key:
            emails = _apollo_search(domain, apollo_key)
            apollo_used += 1

        # 3. No more guessing — if neither API found anything, skip this company
        if not emails:
            logger.debug('No contacts found for %s (%s) — skipping', company['name'], domain)
            skipped += 1
            time.sleep(0.5)
            continue

        contact = _best_contact(emails, company['headcount'])
        if not contact:
            logger.debug('No usable contact for %s (%s)', company['name'], domain)
            skipped += 1
            time.sleep(0.5)
            continue

        first    = (contact.get('first_name') or '').strip()
        last     = (contact.get('last_name')  or '').strip()
        name     = f'{first} {last}'.strip() or None
        role     = contact.get('position') or None
        email    = contact['value']
        verified = (contact.get('confidence') or 0) > 70

        insert_contact(conn, company['id'], name, role, email, verified)
        found += 1
        source = 'apollo' if apollo_used > hunter_used else 'hunter'
        if not contact.get('first_name') and not contact.get('last_name'):
            source = 'guessed'
        logger.info(
            'CONTACT  %-30s → %-40s role=%-30s verified=%s source=%s',
            company['name'], email, role or '—', verified, source,
        )

        time.sleep(0.5)  # Polite rate limit across all sources

    logger.info(
        'Finder done — processed=%d found=%d skipped=%d (Hunter: %d, Apollo: %d)',
        processed, found, skipped, hunter_used, apollo_used,
    )
    return {'processed': processed, 'found': found, 'skipped': skipped}
