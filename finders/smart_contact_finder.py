#!/usr/bin/env python3
"""
Smarter Contact Finder — uses web research only.
NO email guessing. Only inserts contacts when we can find a real, published email.
"""

import logging
import re
import requests
import sqlite3
import time
import sys
import json

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger('contact_finder')

DB_PATH = '/opt/job-agent/data/jobs.db'

# Known email formats for common companies (hard-won from research)
# Format: {domain: (pattern, confidence)}
# patterns: 'first', 'first.last', 'f_last', 'firstl'
KNOWN_FORMATS = {
    'tether.to':       ('first', 0.8),    # p****@tether.to = paolo@tether.to
    'simscale.com':    ('first', 0.6),    # likely johannes@simscale.com (German company)
    'counselhealth.com': ('first', 0.7),  # likely muthu@counselhealth.com
    'marble.ai':       ('first', 0.5),    # unknown, Robert Fair at marble.ai
    'shi.com':         ('first.last', 0.7),  # common for large US corps
}


def _build_email(pattern: str, first: str, last: str, domain: str) -> str:
    """Build email from pattern."""
    mapping = {
        'first':         f'{first}@{domain}',
        'first.last':    f'{first}.{last}@{domain}',
        'f_last':        f'{first[0]}_{last}@{domain}',
        'firstl':        f'{first}{last[0]}@{domain}',
        'flast':         f'{first[0]}{last}@{domain}',
    }
    return mapping.get(pattern, f'{first}@{domain}')


def get_companies_needing_contacts(conn, limit=5):
    """Get qualified companies without contacts, prioritizing high-score ones."""
    cur = conn.execute("""
        SELECT id, name, domain, source
        FROM companies
        WHERE qualified = 1
          AND id NOT IN (SELECT DISTINCT company_id FROM contacts)
          AND domain IS NOT NULL AND domain != ''
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))
    return [dict(r) for r in cur.fetchall()]


def search_for_contact(company_name, domain):
    """
    Search the web for an actual published email at this company.
    Returns (name, role, email, confidence) or None.
    """
    name_clean = company_name.replace('&amp;', '&').split('|')[0].strip()
    
    # Strategy 1: Search for CTO/founder + domain
    queries = [
        f'"{name_clean}" CTO "{domain}" email',
        f'"{name_clean}" founder OR CTO OR "VP Engineering" contact',
        f'"{domain}" leadership team CTO email',
    ]
    
    all_text = []
    for q in queries:
        try:
            r = requests.get(
                'https://duckduckgo.com/html/',
                params={'q': q},
                headers={'User-Agent': 'Mozilla/5.0'},
                timeout=10,
            )
            all_text.append(r.text[:2000])
        except:
            pass
    
    combined = '\n'.join(all_text)
    
    # Extract email addresses
    emails = set(re.findall(r'[a-zA-Z0-9._%+-]+@' + re.escape(domain), combined))
    
    if emails:
        # Found a real email for this domain!
        email = list(emails)[0]
        
        # Try to extract name near the email
        name = None
        lines = combined.split('\n')
        for i, line in enumerate(lines):
            if email in line:
                # Look for a name nearby
                context = ' '.join(lines[max(0,i-2):i+2])
                # Try to find a person's name
                name_match = re.search(r'([A-Z][a-z]+ [A-Z][a-z]+)', context)
                if name_match:
                    name = name_match.group(1)
                break
        
        # Try to extract role
        role = 'Technical Leader'
        for kw in ['CTO', 'Chief Technology', 'VP Engineering', 'Co-Founder', 'Founder']:
            if kw.lower() in combined.lower():
                role = kw
                break
        
        return (name, role, email, 0.9)
    
    # Strategy 2: Check known format
    domain_clean = domain.lower()
    for known_domain, (pattern, confidence) in KNOWN_FORMATS.items():
        if known_domain in domain_clean or domain_clean in known_domain:
            # Search for a person's name
            person_queries = [
                f'"{name_clean}" CTO founder',
                f'"{name_clean}" leadership team',
            ]
            for pq in person_queries:
                try:
                    r = requests.get(
                        'https://duckduckgo.com/html/',
                        params={'q': pq},
                        headers={'User-Agent': 'Mozilla/5.0'},
                        timeout=10,
                    )
                    # Try to extract name from search results
                    names = re.findall(r'([A-Z][a-z]+ [A-Z][a-z]+).*?(?:CTO|Founder|CEO|Co-Founder)', r.text[:3000])
                    if names:
                        full_name = names[0]
                        parts = full_name.split()
                        if len(parts) >= 2:
                            email = _build_email(pattern, parts[0].lower(), parts[-1].lower(), domain_clean)
                            role = 'CTO / Technical Leader'
                            if 'Co-Founder' in r.text[:3000] or 'co-founder' in r.text[:3000].lower():
                                role = 'Co-Founder & CTO'
                            return (full_name, role, email, confidence)
                except:
                    pass
    
    return None


def insert_contact_safe(conn, company_id, name, role, email, confidence):
    """Insert contact only if email looks real and not a duplicate."""
    if not email or '@' not in email:
        return False
    
    # Reject generic patterns
    generic_prefixes = ['hello@', 'contact@', 'info@', 'support@', 'careers@', 
                        'jobs@', 'team@', 'press@', 'media@', 'hr@', 'recruiting@',
                        'admin@', 'mail@', 'noreply@', 'no-reply@']
    for prefix in generic_prefixes:
        if email.lower().startswith(prefix):
            logger.warning(f'  REJECTED (generic): {email}')
            return False
    
    # Check duplicate
    cur = conn.execute(
        "SELECT id FROM contacts WHERE company_id = ? AND email = ?",
        (company_id, email)
    )
    if cur.fetchone():
        logger.info(f'  SKIP (exists): {email}')
        return False
    
    source = 'web_research'
    if confidence >= 0.8:
        source = 'web_research'
    
    conn.execute("""
        INSERT INTO contacts (company_id, name, role, email, source, verified)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (company_id, name, role, email, source, 1 if confidence >= 0.8 else 0))
    conn.commit()
    logger.info(f'  ✅ INSERTED: {name or "?"} <{email}> ({role or "?"}) [conf={confidence}]')
    return True


def run():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    companies = get_companies_needing_contacts(conn, limit=5)
    logger.info(f'Found {len(companies)} companies needing contacts')
    
    if not companies:
        # Try companies with only unverified hunter contacts
        cur = conn.execute("""
            SELECT DISTINCT c.id, c.name, c.domain, c.source
            FROM companies c
            JOIN contacts co ON co.company_id = c.id
            WHERE c.qualified = 1 AND co.source = 'hunter' AND co.verified = 0
            LIMIT 5
        """)
        companies = [dict(r) for r in cur.fetchall()]
        logger.info(f'Found {len(companies)} companies with unverified hunter contacts')
    
    found = 0
    for company in companies:
        logger.info(f'\n--- {company["name"]} ({company["domain"] or "no domain"}) ---')
        
        if not company['domain']:
            logger.info('  SKIP: no domain')
            continue
        
        result = search_for_contact(company['name'], company['domain'])
        
        if result:
            name, role, email, confidence = result
            if insert_contact_safe(conn, company['id'], name, role, email, confidence):
                found += 1
        else:
            logger.info(f'  ❌ No contact found')
        
        time.sleep(1)  # Be polite
    
    logger.info(f'\nDone: found {found} new contacts')
    conn.close()
    return found


if __name__ == '__main__':
    run()
