import os
import sqlite3
import time
from pathlib import Path

import requests
import yaml
from flask import Flask, redirect, render_template, request, url_for

DB_PATH = Path('/data/jobs.db')
CONFIG_PATH = Path('/data/config.yml')

HUNTER_API = 'https://api.hunter.io/v2/domain-search'

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev')


def _db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ── Hunter.io helpers (self-contained — agent/finder.py is in a separate container) ──

_ROLE_TIERS = [
    ['cto', 'chief technology officer', 'chief technical officer'],
    ['co-founder', 'cofounder', 'co founder'],
    [
        'head of engineering', 'vp of engineering', 'vp engineering',
        'director of engineering', 'engineering director',
    ],
    ['engineering manager', 'technical manager', 'tech manager'],
    ['tech lead', 'technical lead', 'lead engineer', 'lead developer', 'staff engineer'],
    ['developer', 'software engineer', 'software developer'],
]


def _tier(position: str) -> int:
    if not position:
        return len(_ROLE_TIERS)
    p = position.lower()
    for i, terms in enumerate(_ROLE_TIERS):
        if any(t in p for t in terms):
            return i
    return len(_ROLE_TIERS)


def _best_contact(emails: list) -> dict | None:
    candidates = [e for e in emails if e.get('value')]
    if not candidates:
        return None
    candidates.sort(key=lambda e: (
        0 if e.get('type') == 'personal' else 1,
        _tier(e.get('position', '')),
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
              AND id NOT IN (SELECT DISTINCT company_id FROM contacts)
            ORDER BY remote_score DESC
        """).fetchall()

        email_found = conn.execute("""
            SELECT c.*, ct.email, ct.name AS contact_name, ct.role
            FROM companies c
            JOIN contacts ct ON ct.company_id = c.id
            WHERE c.qualified = 1
              AND c.id NOT IN (
                  SELECT DISTINCT company_id FROM emails
                  WHERE status IN ('sent','replied')
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
              AND id NOT IN (SELECT DISTINCT company_id FROM contacts)
            ORDER BY remote_score DESC
        """).fetchall()

        processed = found = skipped = 0

        for company in targets:
            emails = _hunter_search(company['domain'], api_key)
            processed += 1

            contact = _best_contact(emails)
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
                """
                INSERT INTO contacts (company_id, name, role, email, source, verified)
                VALUES (?, ?, ?, ?, 'hunter', ?)
                """,
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
