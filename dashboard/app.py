import os
import sqlite3
from pathlib import Path

import yaml
from flask import Flask, redirect, render_template, request, url_for

DB_PATH = Path('/data/jobs.db')
CONFIG_PATH = Path('/data/config.yml')

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev')


def _db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ── pipeline ──────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return redirect(url_for('pipeline'))


@app.route('/pipeline')
def pipeline():
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

        conn.close()
    except Exception as exc:
        return render_template('pipeline.html', error=str(exc),
                               discovered=[], qualified=[],
                               email_found=[], contacted=[])

    return render_template('pipeline.html',
                           discovered=discovered,
                           qualified=qualified,
                           email_found=email_found,
                           contacted=contacted,
                           error=None)


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

        totals = {
            'total':       scalar('SELECT COUNT(*) FROM companies'),
            'qualified':   scalar('SELECT COUNT(*) FROM companies WHERE qualified = 1'),
            'rejected':    scalar('SELECT COUNT(*) FROM companies WHERE qualified = 0'),
            'pending':     scalar('SELECT COUNT(*) FROM companies WHERE qualified IS NULL'),
            'with_domain': scalar('SELECT COUNT(*) FROM companies WHERE domain IS NOT NULL'),
            'contacts':    scalar('SELECT COUNT(*) FROM contacts'),
            'sent':        scalar("SELECT COUNT(*) FROM emails WHERE status = 'sent'"),
            'replied':     scalar("SELECT COUNT(*) FROM emails WHERE status = 'replied'"),
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
