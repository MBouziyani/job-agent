import base64
import email.mime.text as _mime
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml
from flask import Flask, redirect, render_template, request, url_for, send_file

logger = logging.getLogger(__name__)

DB_PATH     = Path('/data/jobs.db')
CONFIG_PATH = Path('/data/config.yml')

HUNTER_API    = 'https://api.hunter.io/v2/domain-search'
MAILER_MODEL  = 'claude-sonnet-4-6'
GMAIL_SENDER  = 'mb.bouziyani@gmail.com'

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev')


def _db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ── Hunter.io helpers ─────────────────────────────────────────────────────────

_TIERS_SMALL = [
    ['cto', 'chief technology officer', 'chief technical officer'],
    ['co-founder', 'cofounder', 'co founder'],
    ['head of engineering', 'vp of engineering', 'vp engineering',
     'director of engineering', 'engineering director'],
]

_TIERS_MID = [
    ['engineering manager', 'technical manager', 'tech manager'],
    ['tech lead', 'technical lead', 'lead engineer', 'lead developer', 'staff engineer'],
    ['technical recruiter', 'tech recruiter'],
    ['cto', 'chief technology officer', 'chief technical officer'],
]

_TIERS_LARGE = [
    ['technical recruiter', 'tech recruiter'],
    ['talent acquisition', 'talent partner', 'talent sourcer', 'talent specialist'],
    ['people operations', 'people ops', 'hr manager', 'human resources'],
    ['engineering manager', 'technical manager', 'tech manager'],
]


def _tiers_for(headcount: int | None) -> list:
    if headcount is not None and headcount < 30:
        return _TIERS_SMALL
    if headcount is not None and headcount <= 80:
        return _TIERS_MID
    return _TIERS_LARGE


def _tier(position: str, headcount: int | None) -> int:
    tiers = _tiers_for(headcount)
    if not position:
        return len(tiers)
    p = position.lower()
    for i, terms in enumerate(tiers):
        if any(t in p for t in terms):
            return i
    return len(tiers)


def _best_contact(emails: list, headcount: int | None) -> dict | None:
    candidates = [e for e in emails if e.get('value')]
    if not candidates:
        return None
    candidates.sort(key=lambda e: (
        0 if e.get('type') == 'personal' else 1,
        _tier(e.get('position', ''), headcount),
        -(e.get('confidence') or 0),
    ))
    return candidates[0]


def _hunter_search(domain: str, api_key: str) -> list:
    try:
        resp = requests.get(
            HUNTER_API,
            params={'domain': domain, 'api_key': api_key, 'limit': 10},
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json().get('data', {}).get('emails', [])
    except requests.RequestException:
        pass
    return []


# ── Mailer helpers (self-contained — agent container not reachable) ───────────

_MAILER_PROFILE = """\
Name:     Mohammed Bouziyani
Stack:    Java, Spring Boot, React/TypeScript, Node.js, Docker, PostgreSQL, REST APIs, JWT, JUnit
Location: Morocco — open to remote worldwide

Experience:
  Networia (Feb–May 2025):              task management app, Spring Boot + React, JWT, Docker, SonarQube
  Vision Business Consulting (Mar–Sep 2024): web + mobile apps for enterprise clients in manufacturing and healthcare sectors, 20% perf gain
  Networia (Jun–Aug 2023):              medical practice management system
  FSSM Marrakech (May–Jul 2022):        HR application, React + PHP

Projects:
  Job Search Agent: multi-agent pipeline with Claude API (Anthropic), APScheduler, SQLite, Flask dashboard,
    Docker Compose — deployed on DigitalOcean (4 GB VPS, Ubuntu 24.04) — end-to-end system in production
  E-commerce platform: Spring Boot + React + PostgreSQL + Docker

Education: Computer Science & Information Systems Engineering, Université Privée de Marrakech (2024)
Languages: Arabic (native), French (C1), English (B2)"""

_MAILER_PROOFS = """\
Three proof points — choose the single most relevant one for Line 3:
  A. Networia medical system (Jun–Aug 2023): built a medical practice management system in \
Spring Boot + React with JWT auth, production-deployed — healthcare domain, Java backend depth
  B. Vision Business Consulting (Mar–Sep 2024): delivered web + mobile apps for enterprise clients \
in manufacturing and healthcare sectors, 20% performance gain — enterprise scale, French-language context
  C. Job Search Agent (2025): multi-agent AI pipeline using Claude API (Anthropic), Docker Compose \
on DigitalOcean (4 GB VPS, Ubuntu 24.04), APScheduler, Flask — real LLM integration and \
self-managed production infrastructure"""

_MAILER_ANGLE: dict[str, dict[str, str]] = {
    'AI_COMPANY': {
        'hook_style': (
            'Open with a specific technical observation about this company\'s AI approach — '
            'name their model choice, inference pattern, RAG architecture, fine-tuning strategy, '
            'or an interesting design decision visible in their description. '
            'Frame it as one developer recognising another\'s technical choice, not as praise. '
            'Never open with "I noticed you use AI" or any generic admiration phrase.'
        ),
        'relevance_hint': (
            'how building a production multi-agent Claude API system shows Mohammed understands LLM '
            'pipelines and agentic architecture — not just CRUD backends'
        ),
        'proof_prefer': 'Prefer C (AI/LLM + production deployment). Fall back to A if their stack is more Java-adjacent.',
    },
    'DEVOPS_CLOUD': {
        'hook_style': (
            'Open with a specific infrastructure or scale observation — name a tool, '
            'an orchestration approach, or a deployment pattern visible in their stack. '
            'Frame as a practitioner recognising a specific decision, not general enthusiasm. '
            'Go beyond just naming the tool — say something about what they\'re doing with it.'
        ),
        'relevance_hint': (
            "how Mohammed's self-managed multi-container Docker deployment on a VPS reflects the "
            'same operational mindset their team uses'
        ),
        'proof_prefer': 'Prefer C (Docker Compose + self-managed VPS). Fall back to A if healthcare context is relevant.',
    },
    'FRENCH_STARTUP': {
        'hook_style': (
            'Write the FIRST sentence only in French — make it specific to their product or team '
            '(not a generic compliment — reference something concrete about them). '
            'Then immediately continue in English for the rest of the email. '
            'Example structure: "[One specific French sentence about their product/team]. [English continues...]"'
        ),
        'relevance_hint': (
            'French C1 proficiency, Morocco timezone UTC+1 (full overlap with European working hours), '
            'and direct enterprise project experience at Vision Business Consulting'
        ),
        'proof_prefer': 'Prefer B (enterprise clients + French-language context). Fall back to A if their focus is healthcare.',
    },
    'JAVA_SPRING': {
        'hook_style': (
            'Open by naming a specific technical detail from their stack or description — '
            'a Spring module, an architectural pattern (CQRS, event sourcing, hexagonal architecture), '
            'or a library they use. Show you read their stack, not just their job title. '
            'Be precise — "using Spring Security with OAuth2" beats "I see you use Spring Boot".'
        ),
        'relevance_hint': (
            "how Mohammed's Spring Boot internship (JWT, SonarQube, Docker, PostgreSQL) maps "
            'directly to the depth their backend team needs'
        ),
        'proof_prefer': 'Prefer A (Networia medical, Spring Boot + JWT + SonarQube depth). Fall back to B if their context is enterprise/large-scale.',
    },
    'NODEJS_PYTHON': {
        'hook_style': (
            'Open with a versatility angle — acknowledge their stack choice, then establish that '
            'Mohammed moves fluidly across backend languages rather than needing to learn theirs from scratch. '
            'Do NOT say "I\'m a fast learner" — show it through concrete cross-language work. '
            'Reference the specific framework or language they use.'
        ),
        'relevance_hint': (
            'full-stack versatility — Python in production (multi-module automation pipeline), '
            'React/TypeScript on the frontend, willing to go deep on their stack quickly'
        ),
        'proof_prefer': 'Prefer C (Python + Flask production pipeline). Fall back to A if they have a healthcare angle.',
    },
    'GENERAL_TECH': {
        'hook_style': (
            'Open with something specific about this company — a product decision, recent development, '
            'open-source project, or stack choice visible in the description. '
            'Must be grounded in their info, not a generic opener.'
        ),
        'relevance_hint': "how Mohammed's Java/Spring Boot + React background maps to their specific stack or need",
        'proof_prefer': 'Pick whichever of A, B, or C is most relevant to this company\'s stack, scale, and domain.',
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


def _classify_company(company: dict) -> str:
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


def _build_mailer_prompt(company: dict, category: str) -> str:
    contact_name = company.get('contact_name') or 'the hiring team'
    contact_role = company.get('contact_role') or ''
    to_line = f'{contact_name} ({contact_role})' if contact_role else contact_name
    angle = _MAILER_ANGLE[category]

    return f"""Write a cold outreach email from Mohammed Bouziyani to {to_line} at {company['name']}.

Company info:
  Domain:       {company.get('domain') or 'unknown'}
  Description:  {(company.get('description') or 'not available')[:400]}
  Stack/Tags:   {company.get('stack') or 'unknown'}
  Remote score: {company.get('remote_score', 0)}/10
  Category:     {category}

Mohammed's profile:
{_MAILER_PROFILE}

{_MAILER_PROOFS}
  Preference for this company: {angle['proof_prefer']}

Email structure — follow EXACTLY:
  Line 1 — Hook: {angle['hook_style']}
  Line 2 — Relevance: {angle['relevance_hint']}.
  Line 3 — Proof: Use the proof point selected above. State the outcome concretely in one sentence.
  Line 4 — Ask: "Would a 15-min call make sense?"
  Sign-off: Mohammed Bouziyani | mb.bouziyani@gmail.com | linkedin.com/in/mohammed-bouziyani

Hard rules:
  - Body (excluding sign-off) ≤ 150 words
  - No filler phrases: "I am passionate about", "I hope this finds you well", "excited to"
  - Subject must reference {company['name']} specifically
  - Address {contact_name} by first name if it's a real person's name

Return exactly this JSON (no other text):
{{"subject": "...", "body": "..."}}"""


_FOLLOWUP_SIGN_OFF = 'Mohammed Bouziyani | mb.bouziyani@gmail.com | linkedin.com/in/mohammed-bouziyani'


def _build_followup_prompt(email: dict) -> str:
    contact    = email.get('contact_name') or ''
    first_name = contact.split()[0] if contact else 'there'
    contact_role = email.get('contact_role') or ''
    to_line    = f'{contact} ({contact_role})' if contact_role else (contact or 'the team')

    return f"""Write a 2-3 sentence follow-up email from Mohammed Bouziyani.

Original email:
  To:      {to_line} at {email['company_name']}
  Subject: {email['subject']}
  Sent:    {str(email.get('sent_at', ''))[:10]}

Requirements:
  - Open with a natural one-line reference to the previous email
  - One simple question: did they have a chance to look at it?
  - Keep it light — no pressure, no guilt
  - Address {first_name} by first name
  - End with sign-off: {_FOLLOWUP_SIGN_OFF}

Hard rules:
  - Body (excluding sign-off) ≤ 50 words — 2-3 sentences only
  - No filler: "I hope this finds you well", "circle back", "touch base"
  - Subject must be: Re: {email['subject']}

Return exactly this JSON:
{{"subject": "Re: {email['subject']}", "body": "..."}}"""


def _run_mailer_for(conn, targets, max_drafts: int) -> tuple[int, int]:
    import anthropic
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError('ANTHROPIC_API_KEY not set')

    client  = anthropic.Anthropic(api_key=api_key)
    drafted = skipped = 0

    for company in targets:
        if drafted >= max_drafts:
            break
        category = _classify_company(dict(company))
        logger.info('Mailer (dashboard): %s → category=%s', company['name'], category)
        try:
            msg = client.messages.create(
                model=MAILER_MODEL,
                max_tokens=512,
                system='You are writing a cold outreach email. Respond with valid JSON only — no markdown, no explanation.',
                messages=[{'role': 'user', 'content': _build_mailer_prompt(dict(company), category)}],
            )
            text = msg.content[0].text.strip()
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'\s*```$', '', text)
            result = json.loads(text.strip())
        except Exception as exc:
            logger.warning('Draft generation failed for %s: %s', company['name'], exc)
            skipped += 1
            continue

        if not result.get('subject') or not result.get('body'):
            skipped += 1
            continue

        conn.execute(
            "INSERT INTO emails (company_id, contact_id, subject, body, status) VALUES (?, ?, ?, ?, 'draft')",
            (company['id'], company['contact_id'], result['subject'], result['body']),
        )
        conn.commit()
        drafted += 1
        time.sleep(1)

    return drafted, skipped


# ── Gmail helpers ─────────────────────────────────────────────────────────────

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
    msg['To']      = to_address
    msg['From']    = GMAIL_SENDER
    msg['Subject'] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId='me', body={'raw': raw}).execute()


# ── smart timing helper ───────────────────────────────────────────────────────

_TZ_MAP: dict[str, str] = {
    '.fr':     'Europe/Paris',
    '.ca':     'America/Toronto',
    '.co.uk':  'Europe/London',
    '.de':     'Europe/Berlin',
    '.jp':     'Asia/Tokyo',
    '.au':     'Australia/Sydney',
    '.in':     'Asia/Kolkata',
    '.ch':     'Europe/Zurich',
    '.nl':     'Europe/Amsterdam',
    '.se':     'Europe/Stockholm',
    '.no':     'Europe/Oslo',
    '.dk':     'Europe/Copenhagen',
    '.fi':     'Europe/Helsinki',
    '.ie':     'Europe/Dublin',
    '.be':     'Europe/Brussels',
    '.at':     'Europe/Vienna',
    '.es':     'Europe/Madrid',
    '.it':     'Europe/Rome',
    '.pt':     'Europe/Lisbon',
    '.pl':     'Europe/Warsaw',
}


def _estimate_timezone(domain: str | None) -> str | None:
    """Guess timezone from domain TLD — returns IANA timezone name or None."""
    if not domain:
        return None
    domain = domain.lower().strip()
    for suffix, tz in _TZ_MAP.items():
        if domain.endswith(suffix):
            return tz
    return None


# ── pipeline ──────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return redirect(url_for('pipeline'))


@app.route('/pipeline')
def pipeline():
    msg = request.args.get('msg')
    try:
        conn = _db()

        # 1. Discovered — companies where qualified is NULL
        discovered = conn.execute(
            'SELECT * FROM companies WHERE qualified IS NULL ORDER BY created_at DESC'
        ).fetchall()

        # 2. Qualified — qualified=1, no contact yet
        qualified = conn.execute("""
            SELECT * FROM companies
            WHERE qualified = 1
              AND NOT EXISTS (SELECT 1 FROM contacts WHERE contacts.company_id = companies.id)
            ORDER BY remote_score DESC
        """).fetchall()

        # 3. Contacts Found — qualified + has contact, no email yet
        contacts_found = conn.execute("""
            SELECT c.*, ct.email, ct.name AS contact_name, ct.role
            FROM companies c
            JOIN contacts ct ON ct.company_id = c.id
            WHERE c.qualified = 1
              AND NOT EXISTS (
                  SELECT 1 FROM emails
                  WHERE emails.company_id = c.id
              )
            GROUP BY c.id
            ORDER BY c.remote_score DESC
        """).fetchall()

        # 4. Draft — emails with status='draft'
        draft_emails = conn.execute("""
            SELECT e.*, c.name AS company_name, c.domain AS company_domain,
                   ct.name AS contact_name, ct.email AS contact_email
            FROM emails e
            JOIN companies c ON c.id = e.company_id
            LEFT JOIN contacts ct ON ct.id = e.contact_id
            WHERE e.status = 'draft'
            ORDER BY e.created_at DESC
        """).fetchall()

        # 5. Sent — emails with status='sent'
        sent_emails = conn.execute("""
            SELECT e.id, e.sent_at, e.pipeline_stage, c.name AS company_name,
                   c.domain AS company_domain, ct.name AS contact_name,
                   ct.email AS contact_email
            FROM emails e
            JOIN companies c ON c.id = e.company_id
            LEFT JOIN contacts ct ON ct.id = e.contact_id
            WHERE e.status = 'sent'
            ORDER BY e.sent_at DESC
        """).fetchall()

        # 6. Replied — emails with status='replied'
        replied_emails = conn.execute("""
            SELECT e.id, e.sent_at, e.pipeline_stage, c.name AS company_name,
                   c.domain AS company_domain, ct.name AS contact_name,
                   ct.email AS contact_email
            FROM emails e
            JOIN companies c ON c.id = e.company_id
            LEFT JOIN contacts ct ON ct.id = e.contact_id
            WHERE e.status = 'replied'
            ORDER BY e.sent_at DESC
        """).fetchall()

        # 7. Screening/Interview
        screening_emails = conn.execute("""
            SELECT e.id, e.sent_at, e.status AS email_status, e.pipeline_stage,
                   c.name AS company_name, c.domain AS company_domain,
                   ct.name AS contact_name, ct.email AS contact_email
            FROM emails e
            JOIN companies c ON c.id = e.company_id
            LEFT JOIN contacts ct ON ct.id = e.contact_id
            WHERE e.status IN ('screening', 'interview')
            ORDER BY e.sent_at DESC
        """).fetchall()

        # 8. Offer/Rejected
        terminal_emails = conn.execute("""
            SELECT e.id, e.sent_at, e.status AS email_status, e.pipeline_stage,
                   c.name AS company_name, c.domain AS company_domain,
                   ct.name AS contact_name, ct.email AS contact_email
            FROM emails e
            JOIN companies c ON c.id = e.company_id
            LEFT JOIN contacts ct ON ct.id = e.contact_id
            WHERE e.status IN ('offer', 'rejected')
            ORDER BY e.sent_at DESC
        """).fetchall()

        conn.close()
    except Exception as exc:
        return render_template('pipeline.html', error=str(exc), msg=None,
                               discovered=[], qualified=[], contacts_found=[],
                               draft_emails=[], sent_emails=[], replied_emails=[],
                               screening_emails=[], terminal_emails=[])

    return render_template('pipeline.html',
                           discovered=discovered,
                           qualified=qualified,
                           contacts_found=contacts_found,
                           draft_emails=draft_emails,
                           sent_emails=sent_emails,
                           replied_emails=replied_emails,
                           screening_emails=screening_emails,
                           terminal_emails=terminal_emails,
                           msg=msg,
                           error=None)


# ── review ────────────────────────────────────────────────────────────────────

@app.route('/review')
def review():
    msg = request.args.get('msg')
    try:
        conn = _db()
        drafts = conn.execute("""
            SELECT
                e.*,
                c.name        AS company_name,
                c.domain      AS company_domain,
                c.remote_score,
                c.description AS company_description,
                ct.name       AS contact_name,
                ct.role       AS contact_role,
                ct.email      AS contact_email
            FROM emails e
            JOIN companies c  ON c.id  = e.company_id
            LEFT JOIN contacts ct ON ct.id = e.contact_id
            WHERE e.status = 'draft'
            ORDER BY e.created_at DESC
        """).fetchall()
        conn.close()
    except Exception as exc:
        return render_template('review.html', error=str(exc), drafts=[], msg=None)

    return render_template('review.html', drafts=drafts, msg=msg, error=None)


@app.route('/approve/<int:email_id>', methods=['POST'])
def approve(email_id):
    try:
        conn = _db()
        row = conn.execute("""
            SELECT e.*, ct.email AS to_email
            FROM emails e
            JOIN contacts ct ON ct.id = e.contact_id
            WHERE e.id = ?
        """, (email_id,)).fetchone()

        if not row:
            conn.close()
            return redirect(url_for('review', msg='Email not found'))

        _send_via_gmail(row['to_email'], row['subject'], row['body'])

        now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        conn.execute(
            "UPDATE emails SET status = 'sent', sent_at = ? WHERE id = ?",
            (now, email_id),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        return redirect(url_for('review', msg=f'Send failed: {exc}'))

    return redirect(url_for('review', msg='Email sent successfully'))


@app.route('/skip/<int:email_id>', methods=['POST'])
def skip(email_id):
    try:
        conn = _db()
        conn.execute("UPDATE emails SET status = 'skipped' WHERE id = ?", (email_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass
    return redirect(url_for('review'))


# ── mailer trigger ────────────────────────────────────────────────────────────

@app.route('/run-mailer', methods=['POST'])
def run_mailer():
    try:
        conn = _db()

        try:
            cfg_text = CONFIG_PATH.read_text(encoding='utf-8')
            cfg = yaml.safe_load(cfg_text) or {}
        except Exception:
            cfg = {}
        max_drafts = cfg.get('outreach', {}).get('max_drafts_per_day', 3)

        targets = conn.execute("""
            SELECT
                c.*,
                ct.id    AS contact_id,
                ct.name  AS contact_name,
                ct.role  AS contact_role,
                ct.email AS contact_email
            FROM companies c
            JOIN contacts ct ON ct.company_id = c.id
            WHERE c.qualified = 1
              AND NOT EXISTS (SELECT 1 FROM emails WHERE emails.company_id = c.id)
            ORDER BY c.remote_score DESC
        """).fetchall()

        drafted, skipped = _run_mailer_for(conn, targets, max_drafts)
        conn.close()
    except Exception as exc:
        return redirect(url_for('review', msg=f'Mailer error: {exc}'))

    return redirect(url_for('review',
                            msg=f'Mailer done — {drafted} drafts created ({skipped} skipped)'))


# ── followup trigger ──────────────────────────────────────────────────────────

@app.route('/run-followup', methods=['POST'])
def run_followup():
    import anthropic as _anthropic

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return redirect(url_for('review', msg='ANTHROPIC_API_KEY not set'))

    try:
        conn = _db()

        try:
            cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding='utf-8')) or {}
        except Exception:
            cfg = {}
        days = cfg.get('outreach', {}).get('followup_after_days', 7)

        targets = conn.execute("""
            SELECT
                e.*,
                c.name  AS company_name,
                ct.name AS contact_name,
                ct.role AS contact_role
            FROM emails e
            JOIN companies c  ON c.id  = e.company_id
            LEFT JOIN contacts ct ON ct.id = e.contact_id
            WHERE e.status = 'sent'
              AND e.sent_at <= datetime('now', ?)
              AND NOT EXISTS (
                  SELECT 1 FROM emails e2
                  WHERE e2.company_id = e.company_id
                    AND e2.id != e.id
              )
            ORDER BY e.sent_at ASC
        """, (f'-{days} days',)).fetchall()

        client  = _anthropic.Anthropic(api_key=api_key)
        drafted = skipped = 0

        for row in targets:
            email = dict(row)
            try:
                msg = client.messages.create(
                    model='claude-haiku-4-5-20251001',
                    max_tokens=256,
                    system='You are writing a brief cold email follow-up. Respond with valid JSON only.',
                    messages=[{'role': 'user', 'content': _build_followup_prompt(email)}],
                )
                text = msg.content[0].text.strip()
                text = re.sub(r'^```(?:json)?\s*', '', text)
                text = re.sub(r'\s*```$', '', text)
                result = json.loads(text.strip())
            except Exception:
                skipped += 1
                continue

            if not result.get('subject') or not result.get('body'):
                skipped += 1
                continue

            conn.execute(
                "INSERT INTO emails (company_id, contact_id, subject, body, status) VALUES (?, ?, ?, ?, 'draft')",
                (email['company_id'], email['contact_id'], result['subject'], result['body']),
            )
            conn.commit()
            drafted += 1

        conn.close()
    except Exception as exc:
        return redirect(url_for('review', msg=f'Followup error: {exc}'))

    return redirect(url_for('review',
                            msg=f'Followup done — {drafted} drafts created ({skipped} skipped)'))


# ── finder helpers ─────────────────────────────────────────────────────────────

def _do_hunter_finder(conn, api_key: str) -> tuple[int, int, int]:
    """Hunter.io lookup for all qualified companies without a contact. Returns (found, processed, skipped)."""
    targets = conn.execute("""
        SELECT * FROM companies
        WHERE qualified = 1
          AND domain IS NOT NULL
          AND domain != ''
          AND NOT EXISTS (
              SELECT 1 FROM contacts WHERE contacts.company_id = companies.id
          )
        ORDER BY remote_score DESC
    """).fetchall()

    processed = found = skipped = 0

    for company in targets:
        emails = _hunter_search(company['domain'], api_key)
        processed += 1

        contact = _best_contact(emails, company['headcount'])
        if not contact:
            skipped += 1
            time.sleep(1)
            continue

        first = (contact.get('first_name') or '').strip()
        last  = (contact.get('last_name')  or '').strip()
        name  = f'{first} {last}'.strip() or None
        role  = contact.get('position') or None
        email = contact['value']
        verified = (contact.get('confidence') or 0) > 70

        conn.execute(
            "INSERT INTO contacts (company_id, name, role, email, source, verified) VALUES (?, ?, ?, ?, 'hunter', ?)",
            (company['id'], name, role, email, 1 if verified else 0),
        )
        conn.commit()
        found += 1
        time.sleep(1)

    return found, processed, skipped


_HERMES_FINDER_URL = 'http://host.docker.internal:8765/find-emails'
_HERMES_UNAVAILABLE = 'Hermes finder not available — trigger manually via Telegram'


def _do_hermes_finder() -> tuple[str, str | None]:
    """Call the host-side Hermes HTTP endpoint. Returns (output_snippet, error_or_None)."""
    try:
        resp = requests.post(_HERMES_FINDER_URL, timeout=180)
        snippet = resp.text.strip()[:300]
        if resp.status_code != 200:
            return snippet, f'HTTP {resp.status_code}'
        return snippet, None
    except requests.exceptions.ConnectionError:
        return '', _HERMES_UNAVAILABLE
    except requests.exceptions.Timeout:
        return '', 'Hermes request timed out after 180s'
    except Exception as exc:
        return '', str(exc)


# ── finder trigger ─────────────────────────────────────────────────────────────

@app.route('/run-finder', methods=['POST'])
def run_finder():
    api_key = os.environ.get('HUNTER_API_KEY')
    if not api_key:
        return redirect(url_for('pipeline', msg='HUNTER_API_KEY not set'))

    try:
        conn = _db()
        found, processed, skipped = _do_hunter_finder(conn, api_key)
        conn.close()
    except Exception as exc:
        return redirect(url_for('pipeline', msg=f'Finder error: {exc}'))

    msg = f'Finder done — {found} contacts found ({processed} processed, {skipped} skipped)'
    return redirect(url_for('pipeline', msg=msg))


@app.route('/run-hermes-finder', methods=['POST'])
def run_hermes_finder():
    snippet, error = _do_hermes_finder()
    if error:
        # Pass the unavailable message verbatim; prefix other errors with context.
        msg = error if error == _HERMES_UNAVAILABLE else f'Hermes error — {error}: {snippet}'.rstrip(': ')
        return redirect(url_for('pipeline', msg=msg))
    msg = f'Hermes done — {snippet}' if snippet else 'Hermes done'
    return redirect(url_for('pipeline', msg=msg))


@app.route('/run-all', methods=['POST'])
def run_all():
    parts = []

    # Step 1 — Hunter.io finder
    api_key = os.environ.get('HUNTER_API_KEY')
    if api_key:
        try:
            conn = _db()
            found, processed, _ = _do_hunter_finder(conn, api_key)
            conn.close()
            parts.append(f'Hunter: {found} contacts ({processed} processed)')
        except Exception as exc:
            parts.append(f'Hunter error: {exc}')
    else:
        parts.append('Hunter skipped (no key)')

    # Step 2 — Hermes web-search finder
    snippet, error = _do_hermes_finder()
    parts.append(error if error else (f'Hermes done — {snippet}' if snippet else 'Hermes done'))

    # Step 3 — Mailer draft generation
    if not os.environ.get('ANTHROPIC_API_KEY'):
        parts.append('Mailer skipped (no key)')
    else:
        try:
            conn = _db()
            try:
                cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding='utf-8')) or {}
            except Exception:
                cfg = {}
            max_drafts = cfg.get('outreach', {}).get('max_drafts_per_day', 3)
            targets = conn.execute("""
                SELECT c.*, ct.id AS contact_id, ct.name AS contact_name,
                       ct.role AS contact_role, ct.email AS contact_email
                FROM companies c
                JOIN contacts ct ON ct.company_id = c.id
                WHERE c.qualified = 1
                  AND NOT EXISTS (SELECT 1 FROM emails WHERE emails.company_id = c.id)
                ORDER BY c.remote_score DESC
            """).fetchall()
            drafted, skipped = _run_mailer_for(conn, targets, max_drafts)
            conn.close()
            parts.append(f'Mailer: {drafted} drafts ({skipped} skipped)')
        except Exception as exc:
            parts.append(f'Mailer error: {exc}')

    return redirect(url_for('pipeline', msg=' | '.join(parts)))


# ── companies ─────────────────────────────────────────────────────────────────

@app.route('/companies')
def companies():
    f = request.args.get('f', 'all')
    score_min = request.args.get('score_min', type=float)
    score_max = request.args.get('score_max', type=float)
    tz_filter = request.args.get('timezone', '').strip()
    try:
        conn = _db()

        conditions = []
        params = []

        if f == 'qualified':
            conditions.append('c.qualified = 1')
        elif f == 'rejected':
            conditions.append('c.qualified = 0')
        elif f == 'no_domain':
            conditions.append("(c.domain IS NULL OR c.domain = '')")
        elif f == 'no_url':
            conditions.append("(c.careers_url IS NULL OR c.careers_url = '')")

        if score_min is not None:
            conditions.append('c.remote_score >= ?')
            params.append(score_min)
        if score_max is not None:
            conditions.append('c.remote_score <= ?')
            params.append(score_max)
        if tz_filter:
            conditions.append('c.timezone = ?')
            params.append(tz_filter)

        where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''

        rows = conn.execute(
            f'SELECT * FROM companies c{where} ORDER BY c.remote_score DESC',
            params,
        ).fetchall()

        # Estimate timezone for companies without one
        rows = [dict(row) for row in rows]
        for row in rows:
            if not row.get('timezone') and row.get('domain'):
                estimated = _estimate_timezone(row['domain'])
                if estimated:
                    try:
                        conn.execute(
                            'UPDATE companies SET timezone = ? WHERE id = ? AND (timezone IS NULL OR timezone = ?)',
                            (estimated, row['id'], ''),
                        )
                        conn.commit()
                    except Exception:
                        pass
        conn.close()
    except Exception as exc:
        return render_template('companies.html', error=str(exc), rows=[], f=f,
                               score_min=score_min, score_max=score_max, tz_filter=tz_filter)

    return render_template('companies.html', rows=rows, f=f, error=None,
                           score_min=score_min, score_max=score_max, tz_filter=tz_filter)


@app.route('/force_qualify/<int:company_id>', methods=['POST'])
def force_qualify(company_id):
    try:
        conn = _db()
        conn.execute('UPDATE companies SET qualified = 1 WHERE id = ?', (company_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass
    return redirect(request.referrer or url_for('companies'))


# ── settings ──────────────────────────────────────────────────────────────────

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    message = error = None

    if request.method == 'POST':
        raw = request.form.get('config', '')
        try:
            parsed = yaml.safe_load(raw)
            if not isinstance(parsed, dict):
                raise ValueError('Config must be a YAML mapping')
            CONFIG_PATH.write_text(raw, encoding='utf-8')
            message = 'Saved.'
        except Exception as exc:
            error = str(exc)

    try:
        config_text = CONFIG_PATH.read_text(encoding='utf-8')
    except FileNotFoundError:
        config_text = '# /data/config.yml not found'

    return render_template('settings.html',
                           config_text=config_text,
                           message=message,
                           error=error)


# ── pipeline stage advancement ────────────────────────────────────────────────

_STAGE_TRANSITIONS: dict[str, list[str]] = {
    'sent':      ['replied'],
    'replied':   ['screening', 'rejected'],
    'screening': ['interview', 'rejected'],
    'interview': ['offer', 'rejected'],
}


@app.route('/advance/<int:email_id>', methods=['POST'])
def advance_stage(email_id):
    stage = request.args.get('stage', '')
    if stage not in ['replied', 'screening', 'interview', 'offer', 'rejected']:
        return redirect(url_for('pipeline', msg='Invalid stage'))

    try:
        conn = _db()
        row = conn.execute(
            'SELECT status, pipeline_stage FROM emails WHERE id = ?', (email_id,)
        ).fetchone()

        if not row:
            conn.close()
            return redirect(url_for('pipeline', msg='Email not found'))

        current = row['status']
        allowed = _STAGE_TRANSITIONS.get(current, [])
        if stage not in allowed:
            conn.close()
            return redirect(url_for('pipeline',
                                    msg=f'Cannot go from "{current}" to "{stage}"'))

        now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        conn.execute(
            'UPDATE emails SET status = ?, pipeline_stage = ?, sent_at = ? WHERE id = ?',
            (stage, stage, now, email_id),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        return redirect(url_for('pipeline', msg=f'Advance error: {exc}'))

    return redirect(url_for('pipeline', msg=f'Marked as {stage}'))


# ── direct applications ───────────────────────────────────────────────────────

@app.route('/mark-applied/<int:company_id>', methods=['POST'])
def mark_applied(company_id):
    try:
        conn = _db()
        conn.execute('UPDATE companies SET applied = 1 WHERE id = ?', (company_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass
    return redirect(request.referrer or url_for('companies'))


@app.route('/set-timezone/<int:company_id>', methods=['POST'])
def set_timezone(company_id):
    tz = request.form.get('timezone', '').strip() or None
    try:
        conn = _db()
        conn.execute('UPDATE companies SET timezone = ? WHERE id = ?', (tz, company_id))
        conn.commit()
        conn.close()
    except Exception:
        pass
    return redirect(request.referrer or url_for('companies'))


# ── stats ─────────────────────────────────────────────────────────────────────

@app.route('/stats')
def stats():
    try:
        conn = _db()

        def scalar(sql):
            return conn.execute(sql).fetchone()[0] or 0

        total     = scalar('SELECT COUNT(*) FROM companies')
        rejected  = scalar('SELECT COUNT(*) FROM companies WHERE qualified = 0')
        avg_score = conn.execute(
            'SELECT AVG(remote_score) FROM companies WHERE qualified IS NOT NULL'
        ).fetchone()[0]

        totals = {
            'total':          total,
            'qualified':      scalar('SELECT COUNT(*) FROM companies WHERE qualified = 1'),
            'rejected':       rejected,
            'pending':        scalar('SELECT COUNT(*) FROM companies WHERE qualified IS NULL'),
            'with_domain':    scalar('SELECT COUNT(*) FROM companies WHERE domain IS NOT NULL'),
            'contacts':       scalar('SELECT COUNT(*) FROM contacts'),
            'sent':           scalar("SELECT COUNT(*) FROM emails WHERE status = 'sent'"),
            'replied':        scalar("SELECT COUNT(*) FROM emails WHERE status = 'replied'"),
            'rejection_rate': round(rejected / total * 100, 1) if total > 0 else 0,
            'avg_score':      round(avg_score, 1) if avg_score is not None else 0,
        }

        # Pipeline stage stats
        ps = {
            'draft':      scalar("SELECT COUNT(*) FROM emails WHERE status = 'draft'"),
            'sent':       scalar("SELECT COUNT(*) FROM emails WHERE status = 'sent'"),
            'replied':    scalar("SELECT COUNT(*) FROM emails WHERE status = 'replied'"),
            'screening':  scalar("SELECT COUNT(*) FROM emails WHERE status = 'screening'"),
            'interview':  scalar("SELECT COUNT(*) FROM emails WHERE status = 'interview'"),
            'offer':      scalar("SELECT COUNT(*) FROM emails WHERE status = 'offer'"),
            'rejected_email': scalar("SELECT COUNT(*) FROM emails WHERE status = 'rejected'"),
        }

        # Conversion rates
        sent_total = ps['sent'] + ps['replied'] + ps['screening'] + ps['interview'] + ps['offer'] + ps['rejected_email']
        cr = {}
        cr['reply_rate']    = round(ps['replied'] / sent_total * 100, 1) if sent_total > 0 else 0
        cr['screening_rate'] = round(ps['screening'] / ps['replied'] * 100, 1) if ps['replied'] > 0 else 0
        cr['interview_rate'] = round(ps['interview'] / ps['screening'] * 100, 1) if ps['screening'] > 0 else 0
        cr['offer_rate']     = round(ps['offer'] / ps['interview'] * 100, 1) if ps['interview'] > 0 else 0

        by_source = conn.execute(
            'SELECT source, COUNT(*) n FROM companies GROUP BY source ORDER BY n DESC'
        ).fetchall()

        top = conn.execute("""
            SELECT name, domain, remote_score, source
            FROM companies
            WHERE qualified = 1
            ORDER BY remote_score DESC
            LIMIT 15
        """).fetchall()

        conn.close()
    except Exception as exc:
        return render_template('stats.html', error=str(exc),
                               totals={}, by_source=[], top=[],
                               pipeline_stats={}, conversion_rates={})

    return render_template('stats.html',
                           totals=totals,
                           by_source=by_source,
                           top=top,
                           pipeline_stats=ps,
                           conversion_rates=cr,
                           error=None)


# ── LinkedIn Outreach ─────────────────────────────────────────────────────────

import json as _json
import re as _re

_OUTREACH_SYSTEM = """You are an expert career coach and copywriter. Write a cold outreach email for a job application that is specific, concise, and effective.

Rules:
- NEVER change years of experience or job titles
- Adapt technology keywords to match the job description
- Keep body under 100 words (excluding greeting and sign-off)
- Start with "Hi {firstName}," on its own line
- Reference something specific about the company
- End with: "Would a 15-min call make sense?"
- Sign-off: Mohammed Bouziyani | mb.bouziyani@gmail.com | linkedin.com/in/mohammed-bouziyani
- No filler phrases
- Subject must reference the company

Return JSON: {"subject": "...", "body": "..."}"""

_OUTREACH_CV_SYSTEM = """You are an expert resume writer. Adapt Mohammed's CV to match a job posting.

Rules:
- NEVER change dates, company names, job titles, or education
- DO rewrite technology keywords to match the job
- Make bullets use the exact tech keywords from the job description where plausible

Return JSON with:
- lead_experience: what to put first
- cv_bullets: rewritten bullets per experience
- project_to_emphasize: which project + how to describe it
- ats_keywords_to_include: list of keywords
- experience_order: ordered list
- notes: any ATS tips"""

_BASE_OUTREACH_PROFILE = """Name: Mohammed Bouziyani
Location: Morocco — open to remote worldwide

Experience:
1. Full-stack internship (Feb-May 2025) - Networia: built a task management web app
2. Full-stack developer contract (Mar-Sep 2024) - Vision Business Consulting: web + mobile apps for enterprise clients, 20% perf gain
3. Full-stack internship (Jun-Aug 2023) - Networia: medical practice management system
4. Internship (May-Jul 2022) - FSSM Marrakech: HR application

Projects:
- Job Search Agent: multi-agent AI pipeline (Claude API, APScheduler, SQLite, Flask, Docker Compose, DigitalOcean)
- E-commerce platform: Spring Boot + React + PostgreSQL + Docker

Education: CS & Information Systems Engineering, Universite Privee de Marrakech (2024)
Languages: Arabic (native), French (C1), English (B2)"""


def _call_deepseek(messages: list, system: str, max_tokens: int = 2048) -> str:
    from openai import OpenAI
    import time as _time
    api_key = os.environ.get('DEEPSEEK_API_KEY')
    if not api_key:
        raise RuntimeError('DEEPSEEK_API_KEY not set')
    client = OpenAI(api_key=api_key, base_url='https://api.deepseek.com',
                    timeout=60, max_retries=2)
    
    last_err = None
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model='deepseek-v4-flash',
                max_tokens=max_tokens,
                temperature=0.2,
                messages=[{'role': 'system', 'content': system}] + messages,
            )
            text = resp.choices[0].message.content.strip()
            if text:
                text = _re.sub(r'^```(?:json)?\s*', '', text)
                text = _re.sub(r'\s*```$', '', text)
                return text
            last_err = f'empty response (finish={resp.choices[0].finish_reason})'
        except Exception as e:
            last_err = str(e)
        if attempt < 2:
            _time.sleep(2 ** attempt)  # 1s, 2s backoff
    raise RuntimeError(f'DeepSeek API failed after 3 retries: {last_err}')


@app.route('/outreach', methods=['GET', 'POST'])
def outreach():
    error = msg = None
    analysis = email = cv = None
    job_desc = contact_name = contact_role = ''
    generating = False

    if request.method == 'POST':
        job_desc = request.form.get('job_desc', '').strip()
        contact_name = request.form.get('contact_name', '').strip()
        contact_role = request.form.get('contact_role', '').strip()
        generating = True

        if not job_desc:
            error = 'Please paste a job description'
        else:
            try:
                # Step 1: Analyze
                analysis_raw = _call_deepseek(
                    [{'role': 'user', 'content': f'Analyze this job posting. Return JSON with: company_name, role_title, seniority, main_stack (array), domain (backend/frontend/full-stack/devops/ai/data/cloud), remote_policy, key_requirements (array of 5), role_type_category (JAVA_SPRING/AI_COMPANY/DEVOPS_CLOUD/NODEJS_PYTHON/FRENCH_STARTUP/GENERAL_TECH/FRONTEND/DATA_ENGINEERING).\n\nJob:\n{job_desc[:2500]}'}],
                    'Extract structured info from this job posting. Return JSON.',
                )
                analysis = _json.loads(analysis_raw)

                # Step 2: Generate email
                contact_line = contact_name
                if contact_name and contact_role:
                    contact_line = f'{contact_name} ({contact_role})'
                elif contact_name:
                    contact_line = contact_name
                else:
                    contact_line = 'the hiring team'

                stack_str = ', '.join(analysis.get('main_stack', []))
                reqs_str = '\n'.join(f'- {r}' for r in analysis.get('key_requirements', []))

                email_raw = _call_deepseek(
                    [{'role': 'user', 'content': f'Write a cold outreach email from Mohammed Bouziyani to {contact_line} at {analysis.get("company_name", "the company")}.\n\nRole: {analysis.get("role_title", "?")}\nStack: {stack_str}\nDomain: {analysis.get("domain", "?")}\nCategory: {analysis.get("role_type_category", "GENERAL_TECH")}\nRequirements:\n{reqs_str}\n\nProfile:\n{_BASE_OUTREACH_PROFILE}\n\nReturn JSON: {{"subject": "...", "body": "..."}}'}],
                    _OUTREACH_SYSTEM,
                )
                email = _json.loads(email_raw)

                # Step 3: Generate CV adaptation
                cv_raw = _call_deepseek(
                    [{'role': 'user', 'content': f'Adapt Mohammed\'s CV for:\n\nCompany: {analysis.get("company_name", "?")}\nRole: {analysis.get("role_title", "?")}\nStack: {stack_str}\nCategory: {analysis.get("role_type_category", "GENERAL_TECH")}\nRequirements:\n{reqs_str}\n\nBase CV:\n{_BASE_OUTREACH_PROFILE}\n\nReturn JSON with lead_experience, cv_bullets, project_to_emphasize, ats_keywords_to_include, experience_order, notes. NEVER change dates or company names.'}],
                    _OUTREACH_CV_SYSTEM,
                )
                cv = _json.loads(cv_raw)

            except Exception as exc:
                error = f'Generation failed: {exc}'
            finally:
                generating = False

    return render_template('outreach.html',
                           error=error, msg=msg,
                           analysis=analysis, email=email, cv=cv,
                           job_desc=job_desc,
                           contact_name=contact_name,
                           contact_role=contact_role,
                           generating=generating)


# ── health ────────────────────────────────────────────────────────────────────

@app.route('/health')
def health():
    return {'status': 'ok'}


# ── CV Adapter ─────────────────────────────────────────────────────────────────

_ADAPTED_DIR = Path('/data/cv_adapted')
_ADAPTED_DIR.mkdir(parents=True, exist_ok=True)

CV_EN = '/data/cv_en.pdf'
CV_FR = '/data/cv_fr.pdf'

_CV_ADAPT_SYSTEM = """You are a CV adaptation expert. Analyze a job posting and suggest specific text edits for a PDF CV.

Rules:
- NEVER change dates, company names, job titles, or education
- Technology keyword swaps MUST be 1:1 replacements from existing CV text
- Profile text rewrite: keep the same length (3-4 lines), same tone, reference the target role/company
- Return valid JSON only — no markdown, no explanation

Return JSON: {
  "analysis": {
    "company_name": "...",
    "role_title": "...",
    "seniority": "junior/mid/senior",
    "domain": "backend/frontend/fullstack/devops/ai/data",
    "remote_policy": "remote/hybrid/onsite",
    "key_requirements": ["req1", "req2", "req3", "req4", "req5"]
  },
  "profile": {
    "old_text": "exact original profile text",
    "new_text": ["line1 (max ~95 chars)", "line2 (max ~95 chars)", "line3 (max ~95 chars)"]
  },
  "stack_swaps": [
    {"old": "exact text in CV", "new": "replacement text"}
  ],
  "fixes": [
    {"old": "exact text to fix", "new": "replacement text"}
  ]
}

IMPORTANT: profile.new_text MUST be an array of strings, each line max ~95 characters.
Only include stack_swaps and fixes that actually differ from the original CV text."""


def _adapt_cv_pdf(pdf_path: str, changes: dict) -> str:
    """Apply text changes to a PDF using PyMuPDF. Returns path to adapted PDF."""
    import fitz

    doc = fitz.open(pdf_path)
    page = doc[0]
    
    # 1. Apply simple text replacements (stack swaps + fixes)
    for entry in changes.get('stack_swaps', []) + changes.get('fixes', []):
        old = entry['old']
        new = entry['new']
        if old == new:
            continue
        
        rects = page.search_for(old)
        if not rects:
            continue
        
        for rect in rects:
            # Don't touch the stack line at top (y≈137) - it's decorative
            if rect.y0 > 140 and rect.y0 < 150:
                continue
            page.add_redact_annot(rect, fill=None)  # No white fill - just delete text
        
        page.apply_redactions()
        
        # Insert new text at EVERY redacted position (not just first)
        for rect in rects:
            # Don't touch the stack line at top (y≈137) - it's decorative
            if rect.y0 > 140 and rect.y0 < 150:
                continue
            
            # Calculate font size based on text height
            text_h = rect.y1 - rect.y0
            new_font_size = 7.3 if text_h > 8 else 6.6
            
            # For multi-word replacements, adjust font size to fit
            new_len = len(new)
            old_len = len(old)
            if new_len > old_len and old_len > 0:
                # Tighten font size slightly if new text is longer
                ratio = old_len / new_len
                new_font_size = new_font_size * min(1.0, ratio * 1.2)
                new_font_size = max(new_font_size, 5.0)
            
            page.insert_text(
                fitz.Point(rect.x0, rect.y1 - 1.5),
                new,
                fontname="helv",
                fontsize=new_font_size,
                color=(0x0e / 255, 0x0f / 255, 0x0c / 255),
            )
    
    # 2. Replace profile text if provided
    profile = changes.get('profile', {})
    if profile and profile.get('new_text'):
        # Find the profile section bbox
        blocks = page.get_text("dict")["blocks"]
        in_profile = False
        profile_rects = []
        
        for block in blocks:
            if block.get("type") != 0:
                continue
            text = ""
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text += span["text"]
            
            if "# profile" in text or "# profil" in text:
                in_profile = True
                continue
            if "// stack" in text:
                in_profile = False
                continue
            if in_profile and block.get("bbox"):
                profile_rects.append(block["bbox"])
        
        if profile_rects:
            # Redact each line individually (no big white rectangle)
            for rect in profile_rects:
                page.add_redact_annot(fitz.Rect(rect[0], rect[1], rect[2], rect[3]), fill=None)
            page.apply_redactions()
            
            # Get profile text - could be a string or list of lines
            raw = profile['new_text']
            if isinstance(raw, list):
                lines = [l.strip() for l in raw if l.strip()]
            else:
                lines = [l.strip() for l in raw.split('\n') if l.strip()]
            
            # If we got a single long paragraph, wrap it to fit width
            if len(lines) <= 2:
                wrapped = _wrap_text(' '.join(lines), max_chars=95)
                lines = wrapped if wrapped else lines
            
            # Insert line by line
            y_pos = profile_rects[0][1]  # original first line y
            for line_text in lines[:4]:  # max 4 lines like original
                page.insert_text(
                    fitz.Point(54, y_pos + 8),
                    line_text,
                    fontname="helv",
                    fontsize=7.9,
                    color=(0x0e / 255, 0x0f / 255, 0x0c / 255),
                )
                y_pos += 11  # line height at 7.9pt
    
    # Save
    stem = Path(pdf_path).stem
    out_name = f"{stem}_adapted_{int(time.time())}.pdf"
    out_path = _ADAPTED_DIR / out_name
    doc.save(str(out_path))
    doc.close()
    return out_name


def _parse_deepseek_json(text: str) -> dict:
    """Parse JSON from DeepSeek response, handling common LLM issues."""
    import json as _json
    
    text = text.strip()
    
    # Try strict parse first
    try:
        return _json.loads(text)
    except _json.JSONDecodeError:
        pass
    
    # Try to find JSON in markdown code fences
    m = _re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, _re.DOTALL)
    if m:
        try:
            return _json.loads(m.group(1))
        except _json.JSONDecodeError:
            pass
    
    # Try to find any {...} block
    m = _re.search(r'(\{.*\})', text, _re.DOTALL)
    if m:
        candidate = m.group(1)
        # Fix trailing commas (most common LLM issue)
        candidate = _re.sub(r',\s*}', '}', candidate)
        candidate = _re.sub(r',\s*]', ']', candidate)
        try:
            return _json.loads(candidate)
        except _json.JSONDecodeError:
            pass
    
    # Last resort: fix unescaped control chars and try
    cleaned = _re.sub(r'[\x00-\x1f]', '', text)
    cleaned = _re.sub(r',\s*}', '}', cleaned)
    cleaned = _re.sub(r',\s*]', ']', cleaned)
    try:
        return _json.loads(cleaned)
    except _json.JSONDecodeError:
        pass
    
    raise _json.JSONDecodeError(f"Cannot parse DeepSeek response as JSON", text, 0)


def _wrap_text(text: str, max_chars: int = 95) -> list:
    """Wrap text to fit within max_chars per line, breaking at word boundaries."""
    words = text.split()
    lines = []
    current = []
    current_len = 0
    
    for word in words:
        if current_len + len(word) + (1 if current else 0) > max_chars:
            if current:
                lines.append(' '.join(current))
                current = []
                current_len = 0
        current.append(word)
        current_len += len(word) + (1 if len(current) > 1 else 0)
    
    if current:
        lines.append(' '.join(current))
    
    return lines if lines else [text]


@app.route('/cv-adapter', methods=['GET', 'POST'])
def cv_adapter():
    error = msg = None
    analysis = changes = adapted_pdf = None
    new_profile = stack_swaps = None
    changes_list = []
    job_desc = ''
    lang = 'auto'
    detected_lang = ''
    generating = False
    
    if request.method == 'POST':
        job_desc = request.form.get('job_desc', '').strip()
        lang = request.form.get('lang', 'auto')
        fix_alternance = request.form.get('fix_alternance') == '1'
        generating = True
        
        if not job_desc:
            error = 'Please paste a job description'
        else:
            try:
                # Determine which CV to use
                if lang == 'auto':
                    # Detect language from job post
                    detect_lang_prompt = f"""Detect the language of this text. Return one word: "fr" if French, "en" if English.
Text: {job_desc[:500]}"""
                    detected_lang = _call_deepseek(
                        [{'role': 'user', 'content': detect_lang_prompt}],
                        'You detect language. Return one word: en or fr.',
                        max_tokens=64,
                    ).strip().lower()
                    if detected_lang not in ('en', 'fr'):
                        detected_lang = 'en'
                else:
                    detected_lang = lang
                
                cv_path = CV_FR if detected_lang == 'fr' else CV_EN
                
                # Extract current CV text
                import fitz
                cv_doc = fitz.open(cv_path)
                cv_text = cv_doc[0].get_text("text")
                cv_doc.close()
                
                # Call DeepSeek to analyze + suggest changes
                system_prompt = _CV_ADAPT_SYSTEM
                user_prompt = f"""Job Description:
{job_desc[:3000]}

Current CV text ({'French' if detected_lang == 'fr' else 'English'}):
{cv_text[:3000]}

Analyze the job and suggest CV adaptations. Return JSON with analysis, profile rewrite, stack swaps, and fixes.

IMPORTANT rules:
- For stack_swaps: replace outdated/less relevant tech keywords with ones matching the JD
- For profile: rewrite the summary to reference the target role/company
- For fixes: {"change 'alternance'/'Stage' references to 'CDI'/'CDD'/'Contract' wording" if fix_alternance else "fix any mismatches"}
- All replacements must be exact text from the CV PDF"""

                result = _call_deepseek(
                    [{'role': 'user', 'content': user_prompt}],
                    system_prompt,
                    max_tokens=8192,
                )
                
                parsed = _parse_deepseek_json(result)
                analysis = parsed.get('analysis', {})
                changes = parsed
                
                # Store for display
                profile_data = changes.get('profile', {})
                raw_profile = profile_data.get('new_text', '') if profile_data else ''
                if isinstance(raw_profile, list):
                    new_profile = '\n'.join(raw_profile)
                else:
                    new_profile = str(raw_profile)
                stack_swaps = changes.get('stack_swaps', [])
                
                # Build combined changes list for the "Changes Applied" table
                changes_list = []
                for sw in stack_swaps:
                    changes_list.append({'old': sw.get('old', ''), 'new': sw.get('new', '')})
                for fx in changes.get('fixes', []):
                    changes_list.append({'old': fx.get('old', ''), 'new': fx.get('new', '')})
                
                # Apply PDF edits
                adapted_pdf = _adapt_cv_pdf(cv_path, changes)
                
                msg = f'✅ CV adapted for {analysis.get("company_name", "this role")}'
                
            except Exception as exc:
                error = f'Generation failed: {exc}'
                import traceback
                error += f'\n{traceback.format_exc()}'
            finally:
                generating = False
    
    return render_template('cv_adapter.html',
                           error=error, msg=msg,
                           analysis=analysis, changes=changes_list,
                           adapted_pdf=adapted_pdf,
                           new_profile=new_profile,
                           stack_swaps=stack_swaps,
                           job_desc=job_desc,
                           lang=lang,
                           detected_lang=detected_lang,
                           generating=generating)


@app.route('/download/<filename>')
def download_adapted(filename):
    """Serve adapted PDF for download."""
    path = _ADAPTED_DIR / filename
    if not path.exists():
        return {'error': 'File not found'}, 404
    resp = send_file(str(path), as_attachment=True, download_name=filename,
                     mimetype='application/octet-stream')
    resp.headers['Content-Type'] = 'application/octet-stream'
    resp.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp


@app.route('/preview/<filename>')
def preview_adapted(filename):
    """Embed adapted PDF preview."""
    path = _ADAPTED_DIR / filename
    if not path.exists():
        return {'error': 'File not found'}, 404
    return send_file(str(path), mimetype='application/pdf')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
