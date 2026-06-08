import json
import logging
import os
import re
import time

import anthropic

from db import get_companies_for_draft, insert_email_draft

logger = logging.getLogger(__name__)

MODEL = 'claude-sonnet-4-6'

_SENDER          = 'Mohammed Bouziyani'
_SENDER_EMAIL    = 'mb.bouziyani@gmail.com'
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

Projects:
  Job Search Agent: multi-agent pipeline with Claude API (Anthropic), APScheduler, SQLite, Flask dashboard,
    Docker Compose — deployed on DigitalOcean (4 GB VPS, Ubuntu 24.04) — end-to-end system in production
  E-commerce platform: Spring Boot + React + PostgreSQL + Docker

Education: Computer Science & Information Systems Engineering, Université Privée de Marrakech (2024)
Languages: Arabic (native), French (C1), English (B2)\
"""

_SYSTEM = (
    'You are writing a cold outreach email for Mohammed Bouziyani. '
    'Respond with valid JSON only — no markdown fences, no explanation, no extra text.'
)

# Per-category angle: hook_hint, relevance_hint, proof_hint
_ANGLE: dict[str, dict[str, str]] = {
    'AI_COMPANY': {
        'hook_hint': 'their AI product, ML pipeline, LLM integration, or specific model/framework they use',
        'relevance_hint': (
            'how building a production multi-agent Claude API system shows Mohammed understands LLM '
            'pipelines and agentic architecture — not just CRUD backends'
        ),
        'proof_hint': (
            'Job Search Agent: multi-agent pipeline using Claude API (Anthropic), SQLite, Flask, '
            'APScheduler — deployed on DigitalOcean; real LLM integration shipped end-to-end'
        ),
    },
    'DEVOPS_CLOUD': {
        'hook_hint': 'their cloud infrastructure, deployment stack, or a specific DevOps tool or practice they use',
        'relevance_hint': (
            "how Mohammed's self-managed multi-container Docker deployment on a VPS reflects the "
            'same operational mindset their team uses'
        ),
        'proof_hint': (
            'deployed and maintains a multi-container Docker Compose system on DigitalOcean '
            '(Ubuntu 24.04, APScheduler, Flask, SQLite) — self-managed, running in production'
        ),
    },
    'FRENCH_STARTUP': {
        'hook_hint': (
            'their French market, European team structure, or francophone product — '
            'reference something concrete, not just "your company"'
        ),
        'relevance_hint': (
            'French C1 proficiency, Morocco timezone UTC+1 (full overlap with European working hours), '
            'and direct enterprise project experience at Vision Business Consulting'
        ),
        'proof_hint': (
            'built web + mobile apps for BASF and Fondation Mohammed VI at Vision Business '
            'Consulting — enterprise-scale delivery with a 20% performance improvement'
        ),
    },
    'JAVA_SPRING': {
        'hook_hint': 'their Java/Spring backend, microservices architecture, or a specific library or pattern they use',
        'relevance_hint': (
            "how Mohammed's Spring Boot internship (JWT, SonarQube, Docker, PostgreSQL) maps "
            'directly to the depth their backend team needs'
        ),
        'proof_hint': (
            'Spring Boot internship at Networia: task management app with JWT auth, SonarQube '
            'quality gate, Dockerised — production-deployed within a real engineering team'
        ),
    },
    'NODEJS_PYTHON': {
        'hook_hint': (
            'their Node.js or Python stack, a specific framework (Express, FastAPI, Django, NestJS), '
            'or their API architecture'
        ),
        'relevance_hint': (
            'full-stack versatility — Python in production (multi-module automation pipeline), '
            'React/TypeScript on the frontend, willing to go deep on their stack quickly'
        ),
        'proof_hint': (
            'shipped a Python automation pipeline (multi-module, APScheduler, Flask dashboard) '
            'and a Spring Boot + React e-commerce platform — production work across two backend languages'
        ),
    },
    'GENERAL_TECH': {
        'hook_hint': (
            'something specific about them (product feature, recent funding, open-source repo, '
            'blog post, or stack choice) — be concrete, not generic'
        ),
        'relevance_hint': "how Mohammed's Java/Spring Boot + React background maps to their specific stack or need",
        'proof_hint': 'ONE concrete result from Mohammed\'s experience (pick whichever is most relevant to this company)',
    },
}

_AI_KW = [
    'machine learning', 'deep learning', 'neural network', 'natural language processing',
    ' llm', 'large language model', 'generative ai', 'openai', 'langchain', 'hugging face',
    'embedding', 'vector database', ' nlp ', 'ai-powered', 'artificial intelligence',
    'foundation model', 'diffusion model', 'transformer model',
]
_DEVOPS_KW = [
    'kubernetes', ' k8s', 'terraform', 'ansible', 'ci/cd', 'platform engineering',
    'site reliability', ' sre ', 'infrastructure as code', 'helm chart', 'argocd',
    'cloud infrastructure', 'cloudformation', 'pulumi', 'gitops',
]
_FRENCH_KW = [
    'france', 'french', 'paris', 'lyon', 'bordeaux', 'marseille',
    'toulouse', 'nantes', 'strasbourg', 'francophone', 'french-speaking',
]
_JAVA_KW = [
    'spring boot', 'spring framework', 'java backend', 'jvm language', ' kotlin',
    'hibernate', 'micronaut', 'quarkus', 'java microservice',
]
_NODE_PY_KW = [
    'node.js', 'nodejs', 'express.js', 'nestjs', 'next.js',
    'django', 'flask', 'fastapi', 'python backend', 'python api', 'python service',
]


def _classify(company: dict) -> str:
    description = (company.get('description') or '').lower()
    stack = (company.get('stack') or '').lower()
    domain = (company.get('domain') or '').lower()
    text = f' {description} {stack} '

    if any(kw in text for kw in _AI_KW):
        return 'AI_COMPANY'
    if any(kw in text for kw in _DEVOPS_KW):
        return 'DEVOPS_CLOUD'
    if domain.endswith('.fr') or any(kw in text for kw in _FRENCH_KW):
        return 'FRENCH_STARTUP'
    if any(kw in text for kw in _JAVA_KW):
        return 'JAVA_SPRING'
    if any(kw in text for kw in _NODE_PY_KW):
        return 'NODEJS_PYTHON'
    return 'GENERAL_TECH'


def _build_prompt(company: dict, category: str) -> str:
    contact_name = company.get('contact_name') or 'the hiring team'
    contact_role = company.get('contact_role') or ''
    to_line = f'{contact_name} ({contact_role})' if contact_role else contact_name
    angle = _ANGLE[category]

    return f"""Write a cold outreach email from Mohammed Bouziyani to {to_line} at {company['name']}.

Company info:
  Domain:      {company.get('domain') or 'unknown'}
  Description: {(company.get('description') or 'not available')[:400]}
  Stack/Tags:  {company.get('stack') or 'unknown'}
  Remote score: {company.get('remote_score', 0)}/10
  Category:    {category}

Mohammed's profile:
{_PROFILE}

Email structure — follow EXACTLY in this order:
  Line 1 — Hook: {angle['hook_hint']}. Be concrete, not generic.
  Line 2 — Relevance: {angle['relevance_hint']}.
  Line 3 — Proof: {angle['proof_hint']}.
  Line 4 — Ask: "Would a 15-min call make sense?"
  Sign-off: {_SENDER} | {_SENDER_EMAIL} | {_SENDER_LINKEDIN}

Hard rules:
  - Body (excluding sign-off) must be ≤ 150 words
  - No filler: "I am passionate about", "I hope this finds you well", "excited to", "I believe"
  - Subject must reference {company['name']} specifically — no generic "Software Engineer Inquiry"
  - Address {contact_name} by first name if it's a real person's name

Return exactly this JSON (no other text):
{{"subject": "...", "body": "..."}}"""


def _generate(client: anthropic.Anthropic, company: dict, category: str) -> dict | None:
    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=512,
            system=_SYSTEM,
            messages=[{'role': 'user', 'content': _build_prompt(company, category)}],
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

    client     = anthropic.Anthropic()
    max_drafts = cfg.get('outreach', {}).get('max_drafts_per_day', 3)
    companies  = get_companies_for_draft(conn)
    logger.info('Mailer: %d companies eligible for draft (cap=%d)', len(companies), max_drafts)

    drafted = skipped = 0

    for company in companies:
        if drafted >= max_drafts:
            logger.info('Mailer: daily cap of %d reached', max_drafts)
            break

        category = _classify(company)
        logger.info('Mailer: %s → category=%s', company['name'], category)

        result = _generate(client, company, category)
        if not result:
            skipped += 1
            continue

        insert_email_draft(conn, company['id'], company['contact_id'],
                           result['subject'], result['body'])
        drafted += 1
        logger.info('DRAFT  %-30s [%s] → "%s"', company['name'], category, result['subject'])
        time.sleep(1)

    logger.info('Mailer done — drafted=%d skipped=%d', drafted, skipped)
    return {'drafted': drafted, 'skipped': skipped}
