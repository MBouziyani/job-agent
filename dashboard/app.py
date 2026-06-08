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
from flask import Flask, redirect, render_template, request, url_for

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
  Vision Business Consulting (Mar–Sep 2024): web + mobile apps for BASF + Fondation Mohammed VI, 20% perf gain
  Networia (Jun–Aug 2023):              medical practice management system
  FSSM Marrakech (May–Jul 2022):        HR application, React + PHP

Projects:
  Job Search Agent: multi-agent pipeline with Claude API (Anthropic), APScheduler, SQLite, Flask dashboard,
    Docker Compose — deployed on DigitalOcean (4 GB VPS, Ubuntu 24.04) — end-to-end system in production
  E-commerce platform: Spring Boot + React + PostgreSQL + Docker

Education: Computer Science & Information Systems Engineering, Université Privée de Marrakech (2024)
Languages: Arabic (native), French (C1), English (B2)"""

_MAILER_ANGLE: dict[str, dict[str, str]] = {
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
        'proof_hint': "ONE concrete result from Mohammed's experience (pick whichever is most relevant to this company)",
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

Email structure — follow EXACTLY:
  Line 1 — Hook: {angle['hook_hint']}. Be concrete.
  Line 2 — Relevance: {angle['relevance_hint']}.
  Line 3 — Proof: {angle['proof_hint']}.
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


# ── pipeline ──────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return redirect(url_for('pipeline'))


@app.route('/pipeline')
def pipeline():
    msg = request.args.get('msg')
    try:
        conn = _db()
        discovered = conn.execute(
            'SELECT * FROM companies WHERE qualified IS NULL ORDER BY created_at DESC'
        ).fetchall()

        qualified = conn.execute("""
            SELECT * FROM companies
            WHERE qualified = 1
              AND NOT EXISTS (SELECT 1 FROM contacts WHERE contacts.company_id = companies.id)
            ORDER BY remote_score DESC
        """).fetchall()

        email_found = conn.execute("""
            SELECT c.*, ct.email, ct.name AS contact_name, ct.role
            FROM companies c
            JOIN contacts ct ON ct.company_id = c.id
            WHERE c.qualified = 1
              AND NOT EXISTS (
                  SELECT 1 FROM emails
                  WHERE emails.company_id = c.id
                    AND emails.status IN ('sent','replied')
              )
            GROUP BY c.id
            ORDER BY c.remote_score DESC
        """).fetchall()

        contacted = conn.execute("""
            SELECT c.*, e.status, e.sent_at
            FROM companies c
            JOIN emails e ON e.company_id = c.id
            WHERE e.status IN ('sent','replied')
            GROUP BY c.id
            ORDER BY e.sent_at DESC
        """).fetchall()

        all_scraped = conn.execute(
            'SELECT * FROM companies ORDER BY created_at DESC'
        ).fetchall()

        conn.close()
    except Exception as exc:
        return render_template('pipeline.html', error=str(exc), msg=None,
                               discovered=[], qualified=[],
                               email_found=[], contacted=[], all_scraped=[])

    return render_template('pipeline.html',
                           discovered=discovered,
                           qualified=qualified,
                           email_found=email_found,
                           contacted=contacted,
                           all_scraped=all_scraped,
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


# ── finder trigger ─────────────────────────────────────────────────────────────

@app.route('/run-finder', methods=['POST'])
def run_finder():
    api_key = os.environ.get('HUNTER_API_KEY')
    if not api_key:
        return redirect(url_for('pipeline', msg='HUNTER_API_KEY not set'))

    try:
        conn = _db()
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

        conn.close()
    except Exception as exc:
        return redirect(url_for('pipeline', msg=f'Finder error: {exc}'))

    msg = f'Finder done — {found} contacts found ({processed} processed, {skipped} skipped)'
    return redirect(url_for('pipeline', msg=msg))


# ── companies ─────────────────────────────────────────────────────────────────

@app.route('/companies')
def companies():
    f = request.args.get('f', 'all')
    try:
        conn = _db()
        if f == 'qualified':
            rows = conn.execute(
                'SELECT * FROM companies WHERE qualified = 1 ORDER BY remote_score DESC'
            ).fetchall()
        elif f == 'rejected':
            rows = conn.execute(
                'SELECT * FROM companies WHERE qualified = 0 ORDER BY remote_score DESC'
            ).fetchall()
        elif f == 'no_domain':
            rows = conn.execute(
                'SELECT * FROM companies WHERE domain IS NULL ORDER BY created_at DESC'
            ).fetchall()
        else:
            rows = conn.execute(
                'SELECT * FROM companies ORDER BY created_at DESC'
            ).fetchall()
        conn.close()
    except Exception as exc:
        return render_template('companies.html', error=str(exc), rows=[], f=f)

    return render_template('companies.html', rows=rows, f=f, error=None)


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
                               totals={}, by_source=[], top=[])

    return render_template('stats.html',
                           totals=totals,
                           by_source=by_source,
                           top=top,
                           error=None)


# ── health ────────────────────────────────────────────────────────────────────

@app.route('/health')
def health():
    return {'status': 'ok'}


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
