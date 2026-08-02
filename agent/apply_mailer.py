"""
Application Mailer (Plan B) — sends JOB-SPECIFIC applications.

For each open job (Greenhouse/Lever/Ashby) with no application sent yet:
  1. Find the company's recruiting email (verified contacts first, else skip)
  2. Generate a targeted application email that mentions the SPECIFIC role
  3. Insert as draft — auto_sender delivers it (Tue/Wed/Thu, 8-10am)

This is the "apply to the actual offer" track: instead of a generic cold
email, we apply to the exact job the company posted.
"""
import json
import logging
import os
import re
import sqlite3
import time
from typing import Any

from openai import OpenAI

logger = logging.getLogger(__name__)

_DEEPSEEK_BASE = os.environ.get('DEEPSEEK_API_BASE', 'https://api.deepseek.com')
_MODEL = os.environ.get('DEEPSEEK_MODEL', 'deepseek-chat')
_MAX_TOKENS = 400

# Verified contact email types we want (recruiting / people, not generic)
PREFERRED_ROLES = ('recruiting', 'hr', 'talent', 'people', 'hiring', 'careers', 'jobs')

_SYSTEM = (
    'You write short, natural job-application emails. Respond with JSON only: '
    '{"subject": "...", "body": "..."}. No markdown, no extra text.'
)


def _candidate_bio() -> str:
    return (
        "Junior software developer (full-stack, React/Node/Python) based in Morocco, "
        "fluent in English and French. Strong self-starter: built automated pipelines, "
        "scrapers, and web apps. Eager to grow into the role."
    )


def _prompt(company_name: str, job_title: str, location: str, dept: str) -> str:
    return f"""Write a job application email from a junior developer.

Company:  {company_name}
Position: {job_title}
Location: {location or 'not specified'}
Dept:     {dept or 'not specified'}

Candidate: {_candidate_bio()}

Rules:
- Professional, warm, concise (120-180 words max)
- Reference the specific position "{job_title}" by name
- Mention 2 concrete skills/experiences that fit this role
- No attachments mention, no salary talk
- End with a simple call to action (open to an interview / call)

Respond with exactly: {{"subject": "<line>", "body": "<3-4 short paragraphs separated by \\n\\n>"}}"""


def _unwrap_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    return text.strip()


def _generate(client: OpenAI, company_name: str, job_title: str, location: str, dept: str) -> dict | None:
    try:
        msg = client.chat.completions.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            messages=[
                {'role': 'system', 'content': _SYSTEM},
                {'role': 'user', 'content': _prompt(company_name, job_title, location, dept)},
            ],
        )
        data = json.loads(_unwrap_json(msg.choices[0].message.content or ''))
        if data.get('subject') and data.get('body'):
            return data
    except Exception as exc:
        logger.debug('application draft failed for %s %s: %s', company_name, job_title, exc)
    return None


def _best_contact(conn: sqlite3.Connection, company_id: int) -> dict | None:
    """Find the best verified contact for a company — recruiting roles preferred."""
    rows = conn.execute("""
        SELECT id, name, email, role, verified FROM contacts
        WHERE company_id = ? AND email IS NOT NULL AND email != ''
        ORDER BY verified DESC, id
    """, (company_id,)).fetchall()
    if not rows:
        return None
    # Prefer verified + recruiting-ish role
    for r in rows:
        role = (r['role'] or '').lower()
        if r['verified'] == 1 and any(p in role for p in PREFERRED_ROLES):
            return r
    for r in rows:
        if r['verified'] == 1:
            return r
    return rows[0]


def run(conn: sqlite3.Connection, cfg: dict[str, Any]) -> dict[str, int]:
    if not os.environ.get('DEEPSEEK_API_KEY'):
        logger.error('DEEPSEEK_API_KEY not set — skipping application mailer')
        return {'drafted': 0, 'no_contact': 0}

    client = OpenAI(api_key=os.environ['DEEPSEEK_API_KEY'], base_url=_DEEPSEEK_BASE)
    max_apps = cfg.get('outreach', {}).get('max_applications_per_day', 5)

    # Open jobs where NO email has been drafted for that company yet — one per company
    jobs = conn.execute("""
        SELECT oj.id, oj.title, oj.location, oj.dept, oj.company_id,
               c.name AS company_name
        FROM open_jobs oj
        JOIN companies c ON c.id = oj.company_id
        WHERE NOT EXISTS (
            SELECT 1 FROM emails e
            WHERE e.company_id = oj.company_id AND e.pipeline_stage = 'application'
        )
        AND oj.id IN (
            SELECT MIN(oj2.id) FROM open_jobs oj2
            WHERE oj2.company_id = oj.company_id
            GROUP BY oj2.company_id
        )
        ORDER BY oj.created_at DESC
        LIMIT ?
    """, (max_apps * 4,)).fetchall()  # fetch extra; some will lack contacts

    drafted = no_contact = 0
    for job in jobs:
        if drafted >= max_apps:
            break
        contact = _best_contact(conn, job['company_id'])
        if not contact:
            no_contact += 1
            continue

        result = _generate(client, job['company_name'], job['title'],
                           job['location'] or '', job['dept'] or '')
        if not result:
            continue

        # Insert as draft, tagged 'application' so we can track this track separately
        conn.execute("""
            INSERT INTO emails (company_id, contact_id, subject, body, status, pipeline_stage)
            VALUES (?, ?, ?, ?, 'draft', 'application')
        """, (job['company_id'], contact['id'], result['subject'], result['body']))
        # mark the job as matched/applied-for
        conn.execute("UPDATE open_jobs SET matched = 1 WHERE id = ?", (job['id'],))
        conn.commit()
        drafted += 1
        logger.info('APP-DRAFT %-28s %-40s → %s', job['company_name'], job['title'][:38], contact['email'])
        time.sleep(1)

    logger.info('Application mailer done — drafted=%d no_contact=%d', drafted, no_contact)
    return {'drafted': drafted, 'no_contact': no_contact}
