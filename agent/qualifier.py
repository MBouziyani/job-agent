import json
import logging
import os
import re
import time

import anthropic

from db import get_unqualified_companies, update_company_qualification

logger = logging.getLogger(__name__)

MODEL = 'claude-haiku-4-5-20251001'

_SYSTEM = (
    'You must respond with valid JSON only, no markdown, no explanation, no extra text. '
    'You are evaluating companies for a junior full-stack developer cold outreach campaign.'
)


def _build_prompt(company: dict, cfg: dict) -> str:
    qual = cfg.get('qualification', {})
    keywords = ', '.join(qual.get('stack_keywords', []))
    excludes = ', '.join(qual.get('exclude_keywords', []))

    return f"""Evaluate this company for cold outreach from a remote junior developer.

Company name: {company['name']}
Domain:       {company['domain'] or 'unknown'}
Description:  {(company['description'] or 'none')[:400]}
Tags/Stack:   {company['stack'] or 'unknown'}
Headcount:    {company['headcount'] or 'unknown'}

Score based ONLY on remote-friendliness potential. Most tech companies hiring on RemoteOK
are at least partially remote. Be generous — a score of 5+ means worth investigating further.
Only score 0-2 for companies that are obviously not remote (restaurants, physical retail,
local services).

Scoring rules (start at 0, max 10):
  +3  headcount 10–80
  -3  headcount > 200
  +2  description signals remote-first ("async", "work from anywhere", "distributed", "no timezone")
  +2  stack overlaps with: {keywords}
  +1  any signal of recent funding or growth
  -∞  INSTANT DISQUALIFY if any of these words appear: {excludes}
      (if disqualified, set score=0 and disqualify=true)

Respond with exactly this JSON format:
{{"score": 7, "remote_friendly": true, "stack_match": 0.8, "reasoning": "one sentence", "disqualify": false}}"""


def _unwrap_json(text: str) -> str:
    """Strip markdown code fences Claude sometimes wraps JSON in."""
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    return text.strip()


def _score_company(client: anthropic.Anthropic, company: dict, cfg: dict) -> dict | None:
    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=256,
            system=_SYSTEM,
            messages=[{'role': 'user', 'content': _build_prompt(company, cfg)}],
        )
        text = _unwrap_json(msg.content[0].text)
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.error('JSON parse failed for %s: %s | raw: %.200s', company['name'], exc, text)
    except anthropic.APIError as exc:
        logger.error('Claude API error for %s: %s', company['name'], exc)
    except Exception as exc:
        logger.error('Unexpected error qualifying %s: %s', company['name'], exc)
    return None


def run(conn, cfg: dict) -> dict:
    if not os.environ.get('ANTHROPIC_API_KEY'):
        logger.error('ANTHROPIC_API_KEY not set — skipping qualification')
        return {'scored': 0, 'qualified': 0, 'disqualified': 0}

    client = anthropic.Anthropic()
    min_score = cfg.get('qualification', {}).get('min_score', 7)
    companies = get_unqualified_companies(conn)
    logger.info('Qualifying %d companies (min_score=%d)', len(companies), min_score)

    scored = qualified = disqualified = 0

    for company in companies:
        result = _score_company(client, company, cfg)
        if result is None:
            continue

        score = float(result.get('score', 0))
        is_disqualified = bool(result.get('disqualify', False))
        is_qualified = not is_disqualified and score >= min_score

        update_company_qualification(conn, company['id'], score, is_qualified)
        scored += 1

        if is_qualified:
            qualified += 1
            logger.info(
                'QUALIFIED  %-30s score=%-2d match=%.2f — %s',
                company['name'], int(score),
                result.get('stack_match', 0),
                result.get('reasoning', ''),
            )
        else:
            disqualified += 1
            logger.debug(
                'rejected   %-30s score=%-2d disqualify=%s',
                company['name'], int(score), is_disqualified,
            )

        time.sleep(0.5)  # stay well within haiku rate limits

    logger.info(
        'Qualification done — scored=%d qualified=%d disqualified=%d',
        scored, qualified, disqualified,
    )
    return {'scored': scored, 'qualified': qualified, 'disqualified': disqualified}
