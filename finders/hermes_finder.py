#!/usr/bin/env python3
"""
Hermes Web Research Contact Finder
Finds real contacts for qualified companies by doing web research.
Replaces hello@domain.com guesses with actual CTOs, VPs, Founders.
"""
import sqlite3
import sys
import os
import re
import json
import time
from urllib.parse import urlparse

DB_PATH = os.environ.get('JOBS_DB_PATH', '/opt/job-agent/data/jobs.db')

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_batch(conn, limit=5):
    """Get next batch of qualified companies with guessed emails."""
    cur = conn.execute("""
        SELECT DISTINCT c.id, c.name, c.domain, c.description, c.job_title
        FROM companies c
        JOIN contacts co ON co.company_id = c.id
        WHERE c.qualified = 1 
          AND co.source = 'hunter'
          AND co.email LIKE 'hello@%'
        ORDER BY c.remote_score DESC
        LIMIT ?
    """, (limit,))
    return [dict(r) for r in cur.fetchall()]

def has_real_contact(conn, company_id):
    """Check if company already has a real (non-guessed) contact."""
    cur = conn.execute("""
        SELECT COUNT(*) as cnt FROM contacts 
        WHERE company_id = ? AND source NOT IN ('hunter', 'guessed')
    """, (company_id,))
    row = cur.fetchone()
    return row['cnt'] > 0

def insert_contact(conn, company_id, name, role, email, source='web_research', verified=False):
    """Insert a new contact if not duplicate."""
    # Check if this email already exists for this company
    cur = conn.execute(
        "SELECT id FROM contacts WHERE company_id = ? AND email = ?",
        (company_id, email)
    )
    if cur.fetchone():
        print(f"  SKIP: {email} already exists")
        return False
    
    conn.execute("""
        INSERT INTO contacts (company_id, name, role, email, source, verified)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (company_id, name, role, email, source, 1 if verified else 0))
    conn.commit()
    print(f"  INSERTED: {name} <{email}> ({role})")
    return True

def guess_email_from_linkedin(name, domain):
    """Try to construct email from name and domain."""
    if not name or not domain:
        return None
    first = name.split()[0].lower() if name.split() else None
    last = name.split()[-1].lower() if len(name.split()) > 1 else None
    if first and last:
        # Try first@domain
        return f"{first}@{domain}"
    return None

def extract_emails(text):
    """Extract email addresses from text."""
    pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    return set(re.findall(pattern, text))

def clean_name(raw):
    """Clean up a name string."""
    if not raw:
        return None
    # Remove common prefixes
    raw = re.sub(r'^(@|•|\||[-–]|\*)\s*', '', raw).strip()
    # Remove titles in parentheses
    raw = re.sub(r'\s*\(.*?\)\s*$', '', raw).strip()
    # Remove URLs
    raw = re.sub(r'https?://\S+', '', raw).strip()
    return raw if len(raw) > 2 else None

if __name__ == '__main__':
    conn = get_conn()
    batch = get_batch(conn, limit=5)
    print(f"Found {len(batch)} companies needing real contacts")
    
    for company in batch:
        print(f"\n{'='*60}")
        print(f"Company: {company['name']} ({company['domain']})")
        print(f"  Score: {company.get('remote_score', '?')}")
        print(f"  Job: {company.get('job_title', 'N/A')[:80]}")
        
        if has_real_contact(conn, company['id']):
            print(f"  Already has real contact, skipping")
            continue
        
        # Output a JSON line that the Hermes cron job can parse
        result = {
            'company_id': company['id'],
            'name': company['name'],
            'domain': company['domain'],
            'description': company.get('description', '')[:200] if company.get('description') else '',
        }
        print(f"NEED_RESEARCH|{json.dumps(result)}")
    
    conn.close()
