import json
import logging
import os
import re
import time

import anthropic

from db import get_companies_for_draft, insert_email_draft

logger = logging.getLogger(__name__)

MODEL = 'claude-sonnet-4-6'

_SENDER        = 'Mohammed Bouziyani'
_SENDER_EMAIL  = 'mb.bouziyani@gmail.com'
_SENDER_LINKEDIN = 'linkedin.com/in/mohammed-bouziyani'

_PROFILE = """\
Name:     Mohammed Bouziyani
Stack:    Java, Spring Boot, React/TypeScript, Node.js, Docker, PostgreSQL, REST APIs, JWT, JUnit
Location: Morocco — open to remote worldwide

Experience:
  Networia (Feb–May 2025):              task management app, Spring Boot + React, JWT, Docker, SonarQube
  Vision Business Consulting (Mar–Sep 2024): web + mobile apps for BASF + Fondation Mohammed VI, 20% perf gain
  Networia (Jun–Aug 2023):              medical practice management system
  FSSM Marrakech (May–Jul 2022):        HR application, React + PHP

Project: e-commerce platform — Spring Boot + React + PostgreSQL + Docker
Education: Computer Science & Information Systems Engineering, Université Privée de Marrakech (2024)
Languages: Arabic (native), French (C1), English (B2)\
"""

_SYSTEM = (
    'You are writing a cold outreach email for Mohammed Bouziyani. '
    'Respond with valid JSON only — no markdown fences, no explanation, no extra text.'
)


def _build_prompt(company: dict) -> str:
    contact_name = company.get('contact_name') or 'the hiring team'
    contact_role = company.get('contact_role') or ''
    to_line = f'{contact_name} ({contact_role})' if contact_role else contact_name

    return f"""Write a cold outreach email from Mohammed Bouziyani to {to_line} at {company['name']}.

Company info:
  Domain:      {company.get('domain') or 'unknown'}
  Description: {(company.get('description') or 'not available')[:400]}
  Stack/Tags:  {company.get('stack') or 'unknown'}
  Remote score: {company.get('remote_score', 0)}/10

Mohammed's profile:
{_PROFILE}

Email structure — follow EXACTLY in this order:
  Line 1 — Hook: one specific thing about {company['name']} (product feature, recent funding, open-source repo, blog post, or stack choice). Be concrete, not generic.
  Line 2 — Relevance: how Mohammed's Java/Spring Boot + React background maps to their specific stack or need.
  Line 3 — Proof: ONE concrete result from Mohammed's experience (pick whichever is most relevant to this company).
  Line 4 — Ask: "Would a 15-min call make sense?"
  Sign-off: {_SENDER} | {_SENDER_EMAIL} | {_SENDER_LINKEDIN}

Hard rules:
  - Body (excluding sign-off) must be ≤ 150 words
  - No filler: "I am passionate about", "I hope this finds you well", "excited to", "I believe"
  - Subject must reference {company['name']} specifically — no generic "Software Engineer Inquiry"
  - Address {contact_name} by first name if it's a real person's name

Return exactly this JSON (no other text):
{{"subject": "...", "body": "..."}}"""


def _generate(client: anthropic.Anthropic, company: dict) -> dict | None:
    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=512,
            system=_SYSTEM,
            messages=[{'role': 'user', 'content': _build_prompt(company)}],
        )
        text = msg.content[0].text.strip()
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        result = json.loads(text.strip())
        if result.get('subject') and result.get('body'):
            return result
        logger.warning('Mailer: incomplete JSON for %s', company['name'])
    except json.JSONDecodeError as exc:
        logger.error('Mailer: JSON parse failed for %s: %s', company['name'], exc)
    except anthropic.APIError as exc:
        logger.error('Mailer: Claude API error for %s: %s', company['name'], exc)
    except Exception as exc:
        logger.error('Mailer: unexpected error for %s: %s', company['name'], exc)
    return None


def run(conn, cfg: dict) -> dict:
    if not os.environ.get('ANTHROPIC_API_KEY'):
        logger.error('ANTHROPIC_API_KEY not set — skipping mailer')
        return {'drafted': 0, 'skipped': 0}

    client    = anthropic.Anthropic()
    max_drafts = cfg.get('outreach', {}).get('max_drafts_per_day', 3)
    companies  = get_companies_for_draft(conn)
    logger.info('Mailer: %d companies eligible for draft (cap=%d)', len(companies), max_drafts)

    drafted = skipped = 0

    for company in companies:
        if drafted >= max_drafts:
            logger.info('Mailer: daily cap of %d reached', max_drafts)
            break

        result = _generate(client, company)
        if not result:
            skipped += 1
            continue

        insert_email_draft(conn, company['id'], company['contact_id'],
                           result['subject'], result['body'])
        drafted += 1
        logger.info('DRAFT  %-30s → "%s"', company['name'], result['subject'])
        time.sleep(1)

    logger.info('Mailer done — drafted=%d skipped=%d', drafted, skipped)
    return {'drafted': drafted, 'skipped': skipped}
