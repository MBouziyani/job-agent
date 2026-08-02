"""
3-email Sequence followup — generates follow-up emails at configurable intervals.

Pipeline runs both step 2 (first followup, after `followup_after_days` days)
and step 3 (final followup, after `followup_after_days * 2` days) each tick.
The DB query in db.py prevents double-sending.
"""
import json
import logging
import os
import re
import time

from openai import OpenAI

from db import get_emails_needing_followup, insert_email_draft

logger = logging.getLogger(__name__)

MODEL = 'deepseek-v4-flash'
_DEEPSEEK_BASE = os.environ.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')

_SIGN_OFF = 'Mohammed Bouziyani | mb.bouziyani@gmail.com | linkedin.com/in/mohammed-bouziyani'

_SYSTEM = (
    'You are writing a brief cold email follow-up. Be concise and natural. '
    'Respond with valid JSON only — no markdown fences, no explanation.'
)


def _build_prompt(email: dict, is_final: bool = False) -> str:
    contact      = email.get('contact_name') or ''
    first_name   = contact.split()[0] if contact else 'there'
    contact_role = email.get('contact_role') or ''
    to_line      = f'{contact} ({contact_role})' if contact_role else (contact or 'the team')
    company      = email.get('company_name') or 'the team'

    if is_final:
        # Step 3 — final closing email
        return f"""Write a short follow-up email from Mohammed Bouziyani to {to_line} at {company}.

Original email:
  To:      {to_line} at {company}
  Subject: {email['subject']}
  Sent:    {str(email.get('sent_at', ''))[:10]}

Requirements:
  - This is the FINAL follow-up. They have not replied to the first one.
  - Open with a polite acknowledgment that this is the last message
  - One sentence restating your interest briefly
  - Close gracefully — leave the door open without pressure
  - Address {first_name} by first name
  - End with sign-off: {_SIGN_OFF}

Hard rules:
  - Body (excluding sign-off) ≤ 40 words
  - No filler: "I hope this finds you well", "touch base", "circle back"
  - Subject must be: Re: {email['subject']}

Return exactly this JSON (no other text):
{{"subject": "Re: {email['subject']}", "body": "..."}}"""

    # Step 2 — standard first follow-up
    return f"""Write a 2-3 sentence follow-up email from Mohammed Bouziyani to {to_line} at {company}.

Original email:
  To:      {to_line} at {company}
  Subject: {email['subject']}
  Sent:    {str(email.get('sent_at', ''))[:10]}

Requirements:
  - Open with a natural one-line reference to the previous email (vary the phrasing — not always "just following up")
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


def _generate(client: OpenAI, email: dict, is_final: bool = False) -> dict | None:
    try:
        msg = client.chat.completions.create(
            model=MODEL,
            max_tokens=2048,
            temperature=0,
            messages=[
                {'role': 'system', 'content': _SYSTEM},
                {'role': 'user', 'content': _build_prompt(email, is_final)},
            ],
        )
        text = msg.choices[0].message.content.strip()
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        result = json.loads(text.strip())
        if result.get('subject') and result.get('body'):
            return result
        logger.warning('Followup: incomplete JSON for %s', email['company_name'])
    except json.JSONDecodeError as exc:
        logger.error('Followup: JSON parse failed for %s: %s', email['company_name'], exc)
    except Exception as exc:
        logger.error('Followup: API error for %s: %s', email['company_name'], exc)
    return None


def run(conn, cfg: dict) -> dict:
    if not os.environ.get('DEEPSEEK_API_KEY'):
        logger.error('DEEPSEEK_API_KEY not set — skipping followup')
        return {'drafted': 0, 'skipped': 0}

    client = OpenAI(api_key=os.environ['DEEPSEEK_API_KEY'], base_url=_DEEPSEEK_BASE)
    days   = cfg.get('outreach', {}).get('followup_after_days', 7)

    drafted = skipped = 0

    # Step 2 — first follow-up (after `days` days)
    targets_step2 = get_emails_needing_followup(conn, days, sequence_step=2)
    logger.info('Followup step 2: %d eligible (after %d days)', len(targets_step2), days)

    for email in targets_step2:
        result = _generate(client, dict(email), is_final=False)
        if not result:
            skipped += 1
            continue

        try:
            insert_email_draft(
                conn,
                email['company_id'],
                email['contact_id'],
                result['subject'],
                result['body'],
                sequence_step=2,
            )
            drafted += 1
            logger.info('FOLLOWUP1 %-30s -> "%s"', email['company_name'], result['subject'])
        except Exception as exc:
            skipped += 1
            logger.warning('FOLLOWUP1 failed for %s: %s', email['company_name'], exc)
        time.sleep(0.5)

    # Step 3 — final follow-up (after `days * 2` days)
    targets_step3 = get_emails_needing_followup(conn, days, sequence_step=3)
    logger.info('Followup step 3: %d eligible (after %d days)', len(targets_step3), days * 2)

    for email in targets_step3:
        result = _generate(client, dict(email), is_final=True)
        if not result:
            skipped += 1
            continue

        try:
            insert_email_draft(
                conn,
                email['company_id'],
                email['contact_id'],
                result['subject'],
                result['body'],
                sequence_step=3,
            )
            drafted += 1
            logger.info('FOLLOWUP2 %-30s -> "%s" (final)', email['company_name'], result['subject'])
        except Exception as exc:
            skipped += 1
            logger.warning('FOLLOWUP2 failed for %s: %s', email['company_name'], exc)
        time.sleep(0.5)

    logger.info('Followup done — drafted=%d skipped=%d', drafted, skipped)
    return {'drafted': drafted, 'skipped': skipped}
