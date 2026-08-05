"""
Auto-Sender — automatically sends approved-quality email drafts at optimal times.

Smart Timing rules:
  - Only sends Tue/Wed/Thu (best open rates for cold email)
  - Only sends between 8:00-10:00 AM in the recipient's timezone
  - Falls back to sending immediately if timezone unknown
  - Never sends more than max_drafts_per_day (from config)
"""
import base64
import email.mime.text as _mime
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

DB_PATH = Path('/data/jobs.db')
GMAIL_SENDER = 'mb.bouziyani@gmail.com'

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '8352220430')

# Best days for cold email (Tue = 1, Wed = 2, Thu = 3)
_OPTIMAL_DAYS = {1, 2, 3}  # Tuesday, Wednesday, Thursday

# Timezone offset map (hours from UTC)
_TZ_OFFSETS = {
    'America/New_York': -5, 'America/Toronto': -5, 'America/Chicago': -6,
    'America/Denver': -7, 'America/Los_Angeles': -8, 'America/Vancouver': -8,
    'America/Sao_Paulo': -3, 'America/Buenos_Aires': -3,
    'Europe/London': 0, 'Europe/Paris': 1, 'Europe/Berlin': 1,
    'Europe/Madrid': 1, 'Europe/Rome': 1, 'Europe/Amsterdam': 1,
    'Europe/Stockholm': 1, 'Europe/Oslo': 1, 'Europe/Copenhagen': 1,
    'Europe/Zurich': 1, 'Europe/Vienna': 1, 'Europe/Brussels': 1,
    'Europe/Dublin': 0, 'Europe/Lisbon': 0,
    'Asia/Dubai': 4, 'Asia/Kolkata': 5.5, 'Asia/Singapore': 8,
    'Asia/Tokyo': 9, 'Asia/Seoul': 9, 'Asia/Shanghai': 8,
    'Asia/Hong_Kong': 8, 'Asia/Jerusalem': 2, 'Asia/Riyadh': 3,
    'Australia/Sydney': 11, 'Australia/Melbourne': 11,
    'Pacific/Auckland': 13, 'Pacific/Honolulu': -10,
    'Africa/Casablanca': 1, 'Africa/Cairo': 2, 'Africa/Johannesburg': 2,
    'Africa/Lagos': 1, 'Africa/Nairobi': 3,
}

_DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']


def _gmail_service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=os.environ.get('GMAIL_REFRESH_TOKEN'),
        client_id=os.environ.get('GMAIL_CLIENT_ID'),
        client_secret=os.environ.get('GMAIL_CLIENT_SECRET'),
        token_uri='https://oauth2.googleapis.com/token',
        scopes=['https://www.googleapis.com/auth/gmail.send'],
    )
    return build('gmail', 'v1', credentials=creds, cache_discovery=False)


def _send_via_gmail(to_address: str, subject: str, body: str) -> None:
    service = _gmail_service()
    msg = _mime.MIMEText(body, 'plain', 'utf-8')
    msg['To'] = to_address
    msg['From'] = GMAIL_SENDER
    msg['Subject'] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId='me', body={'raw': raw}).execute()


def _telegram(msg: str) -> None:
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        requests.post(
            f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'},
            timeout=10,
        )
    except Exception as exc:
        logger.error('Telegram notify failed: %s', exc)


def _is_optimal_time(tz_name: str | None) -> tuple[bool, str]:
    """Check if current UTC time falls in the optimal window for a timezone.
    
    Returns (is_optimal, reason_string).
    Optimal = Tuesday-Thursday, 8:00-10:00 AM local time.
    For None/unknown timezone, returns (True, ...) — send immediately.
    """
    try:
        if tz_name:
            offset = _TZ_OFFSETS.get(tz_name)
            if offset is None:
                return False, f'Unknown timezone: {tz_name}'
        else:
            # No timezone known — send immediately
            return True, 'No timezone — sending now'

        now_utc = datetime.now(timezone.utc)
        # Calculate local time by adding offset hours
        local_ts = now_utc.timestamp() + offset * 3600
        local_dt = datetime.fromtimestamp(local_ts, tz=timezone.utc)
        local_hour = local_dt.hour
        local_weekday = local_dt.weekday()

        if local_weekday in _OPTIMAL_DAYS and 8 <= local_hour < 10:
            return True, f'Optimal: {_DAYS[local_weekday]} {local_hour}:00 in {tz_name}'
        else:
            return False, f'Not optimal: {_DAYS[local_weekday]} {local_hour}:00 in {tz_name} (need Tue-Thu 8-10am)'
    except Exception as exc:
        return False, f'Timezone error: {exc}'


def _domain_has_mx(domain: str) -> bool:
    """Quick check if a domain has mail servers (won't hard-bounce all mail)."""
    try:
        import dns.resolver
        try:
            answers = dns.resolver.resolve(domain, 'MX', lifetime=5)
            return len(answers) > 0
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.exception.Timeout):
            return False
    except ImportError:
        return True  # Can't check, assume valid


def _looks_valid_email(email: str) -> bool:
    """Heuristic check — rejects obviously invalid patterns."""
    if not email or '@' not in email:
        return False
    local, domain = email.rsplit('@', 1)
    if not domain or '.' not in domain:
        return False
    # Reject only no-reply / notifications addresses (never monitored by humans)
    local_lower = local.lower().strip()
    if re.search(r'(noreply|no.reply|donotreply|do_not_reply|notifications?)', local_lower):
        return False
    # Reject low-value generic inboxes that spam-filter cold mail
    if local_lower in ('info', 'contact', 'hello', 'support', 'admin', 'team', 'mail', 'office', 'sales', 'marketing', 'press', 'media', 'legal', 'billing', 'abuse', 'social', 'hi', 'enquiries', 'enquiry', 'general', 'pr'):
        return False
    # Recruiting inboxes (jobs@, careers@, talent@, hr@...) are PUBLISHED on
    # purpose to receive applications — always allowed.
    return True


def run(conn=None, cfg: dict | None = None) -> dict:
    logger.info('Auto-sender starting...')

    import sqlite3
    if conn is None:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        close_conn = True
    else:
        close_conn = False

    try:
        # Load config for max_drafts_per_day
        import yaml
        config_path = Path('/data/config.yml')
        try:
            raw_cfg = yaml.safe_load(config_path.read_text(encoding='utf-8')) or {}
        except Exception:
            raw_cfg = {}
        max_drafts = raw_cfg.get('outreach', {}).get('max_drafts_per_day', 5)

        # Get all drafts with company/contact info
        drafts = conn.execute("""
            SELECT
                e.id,
                e.subject,
                e.body,
                c.name    AS company_name,
                c.timezone,
                c.domain  AS company_domain,
                ct.email  AS contact_email,
                ct.name   AS contact_name
            FROM emails e
            JOIN companies c  ON c.id  = e.company_id
            LEFT JOIN contacts ct ON ct.id = e.contact_id
            WHERE e.status = 'draft'
              AND ct.verified = 1
              AND ct.source IN ('website', 'scraped', 'hunter', 'rekrute')
            ORDER BY c.remote_score DESC
        """).fetchall()

        logger.info('Auto-sender: %d drafts found (cap=%d)', len(drafts), max_drafts)

        sent_count = 0
        skipped_bad_time = 0
        skipped_bad_email = 0
        skipped_cap = 0
        errors = 0

        for draft in drafts:
            if sent_count >= max_drafts:
                skipped_cap = len(drafts) - sent_count
                break

            # 1. Validate email
            to_email = draft['contact_email']
            if not to_email or not _looks_valid_email(to_email):
                skipped_bad_email += 1
                logger.warning('SKIP (bad email) %s → %s', draft['company_name'], to_email)
                continue

            # 2. Check timing
            tz = draft['timezone'] if draft['timezone'] else None
            is_optimal, reason = _is_optimal_time(tz)

            if not is_optimal:
                skipped_bad_time += 1
                logger.debug('SKIP (timing) %s — %s', draft['company_name'], reason)
                continue

            # 3. Send
            try:
                _send_via_gmail(to_email, draft['subject'], draft['body'])
                now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                conn.execute(
                    "UPDATE emails SET status = 'sent', sent_at = ? WHERE id = ?",
                    (now, draft['id']),
                )
                conn.commit()
                sent_count += 1
                logger.info('SENT  %-30s → %s [tz=%s]', draft['company_name'], to_email, tz or 'none')
            except Exception as exc:
                errors += 1
                logger.error('FAIL  %-30s → %s: %s', draft['company_name'], to_email, exc)

        # Send Telegram summary if anything was sent
        if sent_count > 0:
            msg = f"\u2709\ufe0f <b>Auto-sent {sent_count} emails</b>\n{'—' * 20}\n"
            rows = conn.execute("""
                SELECT c.name, ct.email, e.sent_at
                FROM emails e
                JOIN companies c ON c.id = e.company_id
                LEFT JOIN contacts ct ON ct.id = e.contact_id
                WHERE e.status = 'sent'
                  AND e.sent_at >= datetime('now', '-1 hour')
                ORDER BY e.sent_at DESC
            """).fetchall()
            for row in rows:
                msg += f"\n\u2022 {row['name']} ({row['email']})"
            _telegram(msg)

        result = {
            'sent': sent_count,
            'skipped_bad_time': skipped_bad_time,
            'skipped_bad_email': skipped_bad_email,
            'skipped_cap': skipped_cap,
            'errors': errors,
        }
        logger.info('Auto-sender done — %s', result)
        return result

    finally:
        if close_conn:
            conn.close()


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(name)-24s %(levelname)-8s %(message)s',
    )
    result = run()
    print(f'Result: {result}')
