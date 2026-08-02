"""
Headcount Estimator — fills companies.headcount using DeepSeek.

We have zero size data on 3,700+ qualified companies. The LLM estimates
headcount from the company's description/domain in one cheap call, so the
Greenhouse finder can prioritize small/medium companies (10-200 employees).
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
_MAX_TOKENS = 30

_SYSTEM = (
    'You estimate company headcounts. Respond with a single JSON object: '
    '{"headcount": 150}. No markdown, no explanation.'
)

BATCH = 300  # companies per run (keeps runtime sane)


def _prompt(company: dict) -> str:
    return f"""Estimate the number of employees for this company.

Name:        {company['name']}
Domain:      {company['domain'] or 'unknown'}
Description: {(company['description'] or 'none')[:350]}
Stack:       {company['stack'] or 'unknown'}

Rules:
- 1-10 employees: solo/freelance/micro startup
- 10-50: early startup
- 50-200: small-medium company
- 200-500: medium company
- 500-2000: large company
- 2000+: big corporation / multinational
- Unknown/unclear: best guess from the description

Respond with exactly: {{"headcount": <number>}}"""


def _unwrap_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    return text.strip()


def _estimate(client: OpenAI, company: dict) -> int | None:
    try:
        msg = client.chat.completions.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            messages=[
                {'role': 'system', 'content': _SYSTEM},
                {'role': 'user', 'content': _prompt(company)},
            ],
        )
        data = json.loads(_unwrap_json(msg.choices[0].message.content or ''))
        hc = int(data.get('headcount', 0))
        return max(hc, 0)
    except Exception as exc:
        logger.debug('headcount estimate failed for %s: %s', company['name'], exc)
        return None


def run(conn: sqlite3.Connection, cfg: dict[str, Any]) -> dict[str, int]:
    if not os.environ.get('DEEPSEEK_API_KEY'):
        logger.error('DEEPSEEK_API_KEY not set — skipping headcount estimation')
        return {'estimated': 0}

    client = OpenAI(api_key=os.environ['DEEPSEEK_API_KEY'], base_url=_DEEPSEEK_BASE)

    companies = conn.execute("""
        SELECT id, name, domain, description, stack
        FROM companies
        WHERE qualified = 1 AND (headcount IS NULL OR headcount = 0)
        LIMIT ?
    """, (BATCH,)).fetchall()

    if not companies:
        return {'estimated': 0}

    logger.info('Estimating headcount for %d companies…', len(companies))
    done = 0
    for company in companies:
        hc = _estimate(client, dict(company))
        if hc is not None:
            conn.execute(
                'UPDATE companies SET headcount = ? WHERE id = ?',
                (hc, company['id']),
            )
            done += 1
        time.sleep(0.3)

    conn.commit()
    logger.info('Headcount estimation done — %d companies updated', done)
    return {'estimated': done}
