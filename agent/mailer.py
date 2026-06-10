import json
import logging
import os
import re
import time

from openai import OpenAI

from db import get_companies_for_draft, insert_email_draft

logger = logging.getLogger(__name__)

MODEL = 'deepseek-v4-flash'
_DEEPSEEK_BASE = os.environ.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')

_SENDER          = 'Mohammed Bouziyani'
_SENDER_EMAIL    = 'mb.bouziyani@gmail.com'
_SENDER_LINKEDIN = 'linkedin.com/in/mohammed-bouziyani'

_PROFILE = """\
Name:     Mohammed Bouziyani
Stack:    Java, Spring Boot, React/TypeScript, Node.js, Docker, PostgreSQL, REST APIs, JWT, JUnit
Location: Morocco — open to remote worldwide

Experience:
  Full-stack internship (Feb-May 2025):        task management app, Spring Boot + React, JWT, Docker, SonarQube
  Full-stack developer contract (Mar-Sep 2024): web + mobile apps for enterprise clients in manufacturing and healthcare sectors, 20% perf gain
  Full-stack internship (Jun-Aug 2023):        medical practice management system, Spring Boot + React
  Full-stack internship (May-Jul 2022):        HR application, React

Projects:
  Job Search Agent: multi-agent AI pipeline using Claude API (Anthropic), APScheduler, SQLite, Flask dashboard,
    Docker Compose — deployed on DigitalOcean (4 GB VPS, Ubuntu 24.04) — end-to-end system in production
  E-commerce platform: Spring Boot + React + PostgreSQL + Docker

Education: Computer Science & Information Systems Engineering, Universite Privee de Marrakech (2024)
Languages: Arabic (native), French (C1), English (B2)\
"""

_SYSTEM = (
    'You are writing a cold, ultra-concise outreach email for Mohammed Bouziyani. '
    'Every word must earn its place. Be direct, specific, and very short. '
    'Respond with valid JSON only — no markdown fences, no explanation, no extra text.'
)

# All three proof points — company names removed. C is the most stack-agnostic.
_PROOFS = """\
Three proof points — choose the single most relevant one for Line 3:
  A. At an internship, built a medical practice management system in \
Spring Boot + React with JWT auth, production-deployed — healthcare domain, Java backend depth
  B. As a contractor, delivered web + mobile apps for enterprise healthcare \
and manufacturing clients, achieving 20% performance gain — enterprise scale
  C. Built and deployed a multi-agent AI pipeline using Claude API, Docker Compose, \
and Flask on a self-managed VPS — real LLM integration and production infrastructure from scratch"""

# Per-category angle.
# hook_style (Line 1), relevance_hint (Line 2), proof_prefer (Line 3 guidance).
# All categories must work WITHOUT assuming the company uses Java/Spring/React.
_ANGLE: dict[str, dict[str, str]] = {
    'AI_COMPANY': {
        'hook_style': (
            'ONE short sentence (max 15 words). Name a specific technical detail about '
            'their AI approach — model choice, architecture decision, or pattern you recognise. '
            'No setup, no fluff. Then immediately pivot to yourself.'
        ),
        'relevance_hint': (
            'ONE short sentence (max 20 words) connecting Mohammed production AI pipeline to what they do.'
        ),
        'proof_prefer': 'Prefer C (AI/LLM + production deployment). If they have a Java or healthcare connection, A is also valid.',
    },
    'DEVOPS_CLOUD': {
        'hook_style': (
            'ONE short sentence (max 15 words). Name a specific tool or infra pattern '
            'they use. Show you read their stack without rambling.'
        ),
        'relevance_hint': (
            'ONE short sentence (max 20 words) connecting Mohammed Docker/VPS/devops experience to their infra.'
        ),
        'proof_prefer': 'Prefer C (Docker Compose + self-managed VPS).',
    },
    'FRENCH_STARTUP': {
        'hook_style': (
            'ONE short sentence in French (max 15 words) referencing something specific '
            'about their product or team. Then continue in English.'
        ),
        'relevance_hint': (
            'ONE short sentence (max 20 words) about French C1 + Morocco timezone (UTC+1, full overlap with European hours).'
        ),
        'proof_prefer': 'Prefer C (full-stack ownership from infra to UI). If healthcare context, B is also valid.',
    },
    'JAVA_SPRING': {
        'hook_style': (
            'ONE short sentence (max 15 words). Name a specific Spring module or '
            'pattern they use — e.g. Spring Security with OAuth2, or hexagonal architecture. '
            'Be precise and brief. Not "I see you use Spring Boot".'
        ),
        'relevance_hint': (
            'ONE short sentence (max 20 words) connecting Mohammed Spring Boot experience to their backend needs.'
        ),
        'proof_prefer': 'Prefer A (Spring Boot + JWT + production depth). If AI/infra angle works, C is also valid.',
    },
    'NODEJS_PYTHON': {
        'hook_style': (
            'ONE short sentence (max 15 words). Acknowledge their stack choice and '
            'establish that Mohammed works in this space. Show cross-language experience using their framework. '
            'No "fast learner" claims. Focus on the production system he built, not the language.'
        ),
        'relevance_hint': (
            'ONE short sentence (max 20 words) about Python + TypeScript production experience at every layer.'
        ),
        'proof_prefer': 'Prefer C (Python + Flask production pipeline).',
    },
    'GENERAL_TECH': {
        'hook_style': (
            'ONE short sentence (max 15 words). Mention something specific about '
            'their product, stack, or recent development. No generic admiration.'
        ),
        'relevance_hint': (
            'ONE short sentence (max 20 words) connecting Mohammed full-stack + infra experience '
            'to their engineering needs — focus on versatility, not specific frameworks.'
        ),
        'proof_prefer': 'Prefer C — it shows full-stack ownership, infra skills, and self-sufficiency regardless of their specific stack.',
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

Mohammed profile:
{_PROFILE}

{_PROOFS}
  Preference for this company: {angle['proof_prefer']}

Email structure — follow EXACTLY in this order (each item on its own line):
  0. Greeting — "Hi {contact_name.split()[0] if ' ' in contact_name else contact_name}," on its own line
  1. Hook: {angle['hook_style']}
  2. Relevance: {angle['relevance_hint']}.
  3. Proof: One concrete sentence using the selected proof point. State the outcome with no filler.
  4. Ask: "Would a 15-min call make sense?"
  5. Sign-off: {_SENDER} | {_SENDER_EMAIL} | {_SENDER_LINKEDIN}

Hard rules:
  - ALWAYS start body with "Hi [firstName]," on its own line. This is mandatory.
  - Body (greeting + subject + sign-off excluded) must be ≤ 80 words total
  - Each line (1-3) must be ≤ 20 words each
  - No filler: "I am passionate about", "I hope this finds you well", "excited to", "I believe", "I think"
  - No rambling setup sentences. Get to the point in the FIRST sentence.
  - The hook (Line 1) must name something specific about {company['name']} — not a generic "I saw your company"
  - DONT over-emphasize Java/Spring/React if the company uses a different stack. Focus on full-stack depth and production experience.
  - Subject must be short and reference {company['name']} specifically — no generic "Software Engineer Inquiry"
  - Address {contact_name} by first name if it is a real person name

Return exactly this JSON (no other text):
{{"subject": "...", "body": "..."}}"""


def _generate(client: OpenAI, company: dict, category: str) -> dict | None:
    try:
        msg = client.chat.completions.create(
            model=MODEL,
            max_tokens=2048,
            temperature=0,
            messages=[
                {'role': 'system', 'content': _SYSTEM},
                {'role': 'user', 'content': _build_prompt(company, category)},
            ],
        )
        text = msg.choices[0].message.content.strip()
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        result = json.loads(text.strip())
        if result.get('subject') and result.get('body'):
            return result
        logger.warning('Mailer: incomplete JSON for %s', company['name'])
    except json.JSONDecodeError as exc:
        logger.error('Mailer: JSON parse failed for %s: %s', company['name'], exc)
    except Exception as exc:
        logger.error('Mailer: API error for %s: %s', company['name'], exc)
    return None


def run(conn, cfg: dict) -> dict:
    if not os.environ.get('DEEPSEEK_API_KEY'):
        logger.error('DEEPSEEK_API_KEY not set — skipping mailer')
        return {'drafted': 0, 'skipped': 0}

    client     = OpenAI(api_key=os.environ['DEEPSEEK_API_KEY'], base_url=_DEEPSEEK_BASE)
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
