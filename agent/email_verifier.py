"""
Email Verifier — pipeline step that DNS-verifies contacts before emailing.

Runs after finder, before mailer. Marks emails as verified=1 when their
domain has valid MX records. The mailer+auto_sender should only send to
verified contacts.
"""
import logging
import re
import dns.resolver
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

logger = logging.getLogger(__name__)

# Domains that always pass (well-known)
TRUSTED_DOMAINS = {
    'gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com', 'icloud.com',
    'protonmail.com', 'proton.me', 'pm.me', 'fastmail.com', 'zoho.com',
    'aol.com', 'mail.com', 'yandex.com', 'tutanota.com',
}


def _get_mx(domain: str) -> str | None:
    """Get the primary MX server for a domain (free, DNS only)."""
    try:
        records = dns.resolver.resolve(domain, 'MX', lifetime=5)
        mx = sorted(records, key=lambda r: r.preference)
        return str(mx[0].exchange).rstrip('.')
    except dns.resolver.NXDOMAIN:
        return None
    except dns.resolver.NoAnswer:
        # No MX record — try A record as fallback
        try:
            dns.resolver.resolve(domain, 'A', lifetime=3)
            return '__a_record_only__'
        except Exception:
            return None
    except Exception as exc:
        logger.debug('DNS error for %s: %s', domain, exc)
        return None


def _verify_single(email: str) -> tuple[str, bool, str | None]:
    """
    Verify a single email via DNS.
    Returns (email, is_valid, mx_server_or_reason).
    """
    if '@' not in email:
        return (email, False, 'no_at_sign')

    local, domain = email.rsplit('@', 1)

    # Quick format checks
    if len(local) < 2:
        return (email, False, 'local_part_too_short')
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        return (email, False, 'invalid_format')

    # Trusted domains skip DNS check (they always have valid MX)
    if domain.lower() in TRUSTED_DOMAINS:
        return (email, True, 'trusted_domain')

    # DNS MX check
    mx = _get_mx(domain)
    if mx is None:
        return (email, False, 'domain_not_found')
    if mx == '__a_record_only__':
        return (email, True, 'a_record_only')
    return (email, True, mx)


def run(conn: sqlite3.Connection, cfg: dict[str, Any]) -> dict[str, int]:
    """Verify all unverified contacts via DNS. Updates verified=1 in DB for valid ones."""
    
    # Get all unverified contacts with non-generic emails
    cur = conn.execute("""
        SELECT id, email FROM contacts
        WHERE verified = 0
          AND email IS NOT NULL
          AND email NOT LIKE 'hello@%%'
          AND email NOT LIKE 'info@%%'
          AND email NOT LIKE 'hr@%%'
          AND email NOT LIKE 'contact@%%'
          AND email NOT LIKE 'team@%%'
          AND email NOT LIKE 'careers@%%'
          AND email NOT LIKE 'support@%%'
          AND email NOT LIKE 'admin@%%'
          AND email NOT LIKE 'noreply@%%'
          AND email NOT LIKE 'mail@%%'
    """)
    
    rows = cur.fetchall()
    contacts = [{"id": r[0], "email": r[1]} for r in rows]
    logger.info('Verifier: %d contacts to check', len(contacts))
    
    if not contacts:
        return {'checked': 0, 'valid': 0, 'invalid': 0}
    
    valid_count = 0
    invalid_count = 0
    
    # Verify in parallel (10 threads)
    with ThreadPoolExecutor(max_workers=10) as pool:
        fut_map = {pool.submit(_verify_single, c['email']): c for c in contacts}
        
        for f in as_completed(fut_map):
            contact = fut_map[f]
            try:
                email, is_valid, mx = f.result()
                if is_valid:
                    conn.execute(
                        "UPDATE contacts SET verified = 1 WHERE id = ?",
                        (contact['id'],),
                    )
                    valid_count += 1
                else:
                    invalid_count += 1
            except Exception as exc:
                logger.debug('Verifier error for contact %d: %s', contact['id'], exc)
                invalid_count += 1
    
    conn.commit()
    logger.info(
        'Verifier done — checked=%d valid=%d invalid=%d',
        len(contacts), valid_count, invalid_count,
    )
    return {'checked': len(contacts), 'valid': valid_count, 'invalid': invalid_count}
