"""
Reply Monitor — checks Gmail inbox for replies to sent emails,
updates DB pipeline stage, and sends Telegram notification.

Runs as a cron job (recommended: every hour).
"""
import base64
import email.mime.text as _mime
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

DB_PATH = Path('/data/jobs.db')
GMAIL_SENDER = 'mb.bouziyani@gmail.com'
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '8352220430')


def _gmail_service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=os.environ.get('GMAIL_REFRESH_TOKEN'),
        client_id=os.environ.get('GMAIL_CLIENT_ID'),
        client_secret=os.environ.get('GMAIL_CLIENT_SECRET'),
        token_uri='https://oauth2.googleapis.com/token',
        scopes=['https://www.googleapis.com/auth/gmail.readonly'],
    )
    return build('gmail', 'v1', credentials=creds, cache_discovery=False)


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


def _check_replies(conn: sqlite3.Connection) -> int:
    """Check Gmail for replies to sent emails. Returns count of new replies found."""
    service = _gmail_service()

    # Get all sent emails that we haven't detected replies for
    sent_emails = conn.execute("""
        SELECT e.*, c.name AS company_name, ct.name AS contact_name
        FROM emails e
        JOIN companies c ON c.id = e.company_id
        LEFT JOIN contacts ct ON ct.id = e.contact_id
        WHERE e.status = 'sent'
          AND e.sequence_step = 1
        ORDER BY e.sent_at DESC
    """).fetchall()

    if not sent_emails:
        logger.info('No sent emails to check replies for')
        return 0

    found = 0
    for email in sent_emails:
        email = dict(email)
        # Search for replies matching the subject
        subject = email['subject']
        # Strip Re: prefixes to match threads
        clean_subject = re.sub(r'^Re:\s*', '', subject, flags=re.IGNORECASE).strip()

        query = f'from:*@* to:{GMAIL_SENDER} subject:"{clean_subject}"'
        try:
            results = service.users().messages().list(
                userId='me', q=query, maxResults=5
            ).execute()
        except Exception as exc:
            logger.error('Gmail search failed for "%s": %s', clean_subject, exc)
            continue

        messages = results.get('messages', [])
        if not messages:
            continue

        # Found a reply — update DB
        conn.execute(
            "UPDATE emails SET status = 'replied', pipeline_stage = 'replied' WHERE id = ?",
            (email['id'],),
        )
        conn.commit()
        found += 1

        company = email.get('company_name') or 'Unknown'
        contact = email.get('contact_name') or 'Someone'
        _telegram(
            f"\u2709\ufe0f <b>Reply detected!</b>\n\n"
            f"<b>{contact}</b> from <b>{company}</b> replied to your email!\n"
            f"Subject: {email['subject']}\n\n"
            f"Check your inbox: https://mail.google.com/"
        )
        logger.info('REPLY  %-30s replied to \"%s\"', company, email['subject'])
        time.sleep(1)  # Gmail API rate limit

    return found


def run() -> dict:
    logger.info('Reply monitor starting...')
    init_db()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        found = _check_replies(conn)
    finally:
        conn.close()
    logger.info('Reply monitor done — %d new replies detected', found)
    return {'checked': True, 'found': found}


def init_db() -> None:
    """Ensure DB exists without full init (already handled by agent)"""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(name)-24s %(levelname)-8s %(message)s',
    )
    result = run()
    print(f'Result: {result}')
    if result['found']:
        print(f'Found {result["found"]} new replies!')
