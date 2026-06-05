import json
import logging
import os
import re
import time

import anthropic

from db import get_emails_needing_followup, insert_email_draft

logger = logging.getLogger(__name__)

# Haiku is sufficient for 2-3 line follow-ups; saves quota for mailer's sonnet calls.
MODEL = 'claude-haiku-4-5-20251001'

_SIGN_OFF = 'Mohammed Bouziyani | mb.bouziyani@gmail.com | linkedin.com/in/mohammed-bouziyani'

_SYSTEM = (
    'You are writing a brief cold email follow-up. '
    'Respond with valid JSON only — no markdown fences, no explanation.'
)


def _build_prompt(email: dict) -> str:
    contact      = email.get('contact_name') or ''
    first_name   = contact.split()[0] if contact else 'there'
    contact_role = email.get('contact_role') or ''
    to_line      = f'{contact} ({contact_role})' if contact_role else (contact or 'the team')

    return f"""Write a 2-3 sentence follow-up email from Mohammed Bouziyani.

Original email:
  To:      {to_line} at {email['company_name']}
  Subject: {email['subject']}
  Sent:    {str(email.get('sent_at', ''))[:10]}

Requirements:
  - Open with a natural one-line reference to the previous email
    (e.g. "Just following up on my note from last week" — vary the phrasing)
  - One simple question: did they have a chance to look at it?
  - Keep it light — no pressure, no guilt, no "Did you miss my email?"
  - Address {first_name} by first name
  - End with sign-off: {_SIGN_OFF}

Hard rules:
  - Body (excluding sign-off) ≤ 50 words — 2-3 sentences only
  - No filler: "I hope this finds you well", "I wanted to circle back", "touch base"
  - Subject must be: Re: {email['subject']}

Return exactly this JSON (no other text):
{{"subject": "Re: {email['subject']}", "body": "..."}}"""


def _generate(client: anthropic.Anthropic, email: dict) -> dict | None:
    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=256,
            system=_SYSTEM,
            messages=[{'role': 'user', 'content': _build_prompt(email)}],
        )
        text = msg.content[0].text.strip()
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        result = json.loads(text.strip())
        if result.get('subject') and result.get('body'):
            return result
        logger.warning('Followup: incomplete JSON for %s', email['company_name'])
    except json.JSONDecodeError as exc:
        logger.error('Followup: JSON parse failed for %s: %s', email['company_name'], exc)
    except anthropic.APIError as exc:
        logger.error('Followup: Claude API error for %s: %s', email['company_name'], exc)
    except Exception as exc:
        logger.error('Followup: unexpected error for %s: %s', email['company_name'], exc)
    return None


def run(conn, cfg: dict) -> dict:
    if not os.environ.get('ANTHROPIC_API_KEY'):
        logger.error('ANTHROPIC_API_KEY not set — skipping followup')
        return {'drafted': 0, 'skipped': 0}

    client = anthropic.Anthropic()
    days   = cfg.get('outreach', {}).get('followup_after_days', 7)
    emails = get_emails_needing_followup(conn, days)
    logger.info(
        'Followup: %d sent emails eligible (>%d days old, no reply, no existing followup)',
        len(emails), days,
    )

    drafted = skipped = 0

    for email in emails:
        result = _generate(client, email)
        if not result:
            skipped += 1
            continue

        insert_email_draft(
            conn,
            email['company_id'],
            email['contact_id'],
            result['subject'],
            result['body'],
        )
        drafted += 1
        logger.info('FOLLOWUP %-30s → "%s"', email['company_name'], result['subject'])
        time.sleep(0.5)

    logger.info('Followup done — drafted=%d skipped=%d', drafted, skipped)
    return {'drafted': drafted, 'skipped': skipped}
