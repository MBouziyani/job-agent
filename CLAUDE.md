# CLAUDE.md — Job Search Agent

## Session 1 status (completed 2026-06-05)

### What was built
- `agent/db.py` — SQLite schema init: companies, contacts, emails, followups tables
- `agent/config.py` — loads `data/config.yml` via `load_config()`
- `agent/scraper.py` — RemoteOK (JSON API) + We Work Remotely (RSS) scrapers with deduplication
- `agent/main.py` — entry point: infinite loop, runs pipeline then `time.sleep(86400)`
- `docker-compose.yml` — agent + dashboard containers skeleton, `/data` shared volume

### What works
- RemoteOK: hits JSON API, fixes encoding with `resp.encoding='utf-8'` + `_fix_encoding()` per name,
  skips legal notice (no `company` key), deduplicates by name, stores `domain=None` initially,
  then immediately calls Clearbit Autocomplete to discover real domain and updates the row
- We Work Remotely: fetches RSS, parses with BeautifulSoup xml parser, extracts company from
  title ("... at Company Name"), same Clearbit lookup after insert — no auth, no JS, no blocking
- Clearbit Autocomplete: `https://autocomplete.clearbit.com/v1/companies/suggest?query=NAME`
  free, no key, returns `[{domain, name, logo}, ...]` — take `results[0].domain` if present
- Deduplication: `company_exists(conn, name, source)` checks `UNIQUE(name, source)` in DB
- `main.py` loops forever — run pipeline, sleep 24h, repeat (APScheduler replaces this in Session 6)

### DB schema decisions
- `companies.domain` is nullable — populated by Clearbit at scrape time when found; NULL remainder
  handled by `finder.py` in Session 4 via Hunter.io company search.
- Unique constraint is `UNIQUE(name, source)` — not on `domain`.
- **If schema changed on an existing DB**: delete `/data/jobs.db` and restart to recreate.

### Confirmed RemoteOK API fields (verified via curl)
`slug, id, epoch, date, company, company_logo, position, tags, description, location, apply_url, salary_min, salary_max, logo, url`
Note: `url` is the remoteok.com job page URL — DO NOT use for domain/dedup. Use `company` name.

### Bugs fixed this session
- `scraper.py` referenced undefined `_HEADERS` — silently returned 0. Fixed.
- `main.py` exited after one run. Fixed: `while True` + `time.sleep(86400)`.
- RemoteOK: `url` field points to remoteok.com → dedup collapsed all records to 1. Fixed.
- RemoteOK: UTF-8 mojibake on company names — fixed with `resp.encoding='utf-8'` AND
  `_fix_encoding(s)` helper: `s.encode('latin-1').decode('utf-8')` catches strings already mangled.
- RemoteOK: fake `{slug}.remoteok` domains — removed; Clearbit fills real domain post-insert.
- Himalayas returned 403 consistently — replaced entirely with We Work Remotely RSS scraper.
- `docker-compose.yml` had obsolete `version: '3.8'` top-level key — removed.

### Not yet built (do NOT re-implement)
- qualifier.py, finder.py, mailer.py, followup.py — Session 2+
- Flask dashboard — Session 3
- Wellfound scraper — deferred (requires Playwright)

---

## What this is
A proactive cold outreach system for Mohammed Bouziyani.
Junior full-stack dev (Java/Spring Boot + React), based in Morocco,
targeting remote-worldwide companies/startups.
Goal: scrape companies, qualify them, find contact emails,
generate personalised cold emails, send, track, follow up.
NOT applying to job postings — creating opportunities that don't exist yet.

## Infrastructure
- VPS: DigitalOcean 4GB RAM / 90GB SSD / Ubuntu 24.04 / Frankfurt
- Deployment: Docker Compose (two containers + shared volume)
- Local dev: same Docker Compose runs identically on laptop
- Phase 2: Hermes Agent added to same Compose (after core works)

## Architecture

### Containers
agent       → Python scheduler + all pipeline modules
dashboard   → Flask on port 5000 (review + approve emails)

### Shared volume
/data/jobs.db    → SQLite, single source of truth
/data/config.yml → pipeline settings, editable without redeployment

### File structure
job-agent/
├── docker-compose.yml
├── .env                  # never commit
├── .env.example
├── CLAUDE.md
├── agent/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py           # entry point, APScheduler, runs pipeline daily 8am
│   ├── scraper.py        # pulls companies from sources
│   ├── qualifier.py      # scores companies via Claude API
│   ├── finder.py         # finds contact emails via Hunter.io
│   ├── mailer.py         # generates drafts via Claude API
│   ├── followup.py       # queues day-7 follow-ups
│   └── config.py         # loads config.yml, used by all modules
├── dashboard/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app.py
│   └── templates/
│       ├── base.html
│       ├── pipeline.html  # kanban overview
│       ├── review.html    # approve/edit/reject drafts
│       ├── settings.html  # edit config.yml from browser
│       └── stats.html     # weekly numbers
└── data/
    ├── jobs.db
    └── config.yml

## Database schema

### companies
id, name, domain, website, headcount, countries_count,
stack, description, source, remote_score, created_at

### contacts
id, company_id, name, role, email, source, verified (bool)

### emails
id, company_id, contact_id, subject, body,
status (draft/approved/sent/replied/skipped),
sent_at, opened_at, created_at

### followups
id, email_id, scheduled_at, sent_at, status

## Scoring logic (qualifier.py)
Threshold to proceed: score >= 7 (configurable in config.yml)

+3  headcount between 10-80
-3  headcount > 200 (likely multinational, skip)
+2  job desc contains "async", "work from anywhere", "no timezone"
+2  uses Deel or Remote.com (detected on careers page)
+2  employees in 5+ countries AND headcount < 100
-1  employees in 5+ countries AND headcount > 500
+2  stack match score > 60% (Java/Spring Boot/React keywords)
+1  funded in last 6 months (Crunchbase signal)
 0  instant disqualify if "us only", "must be in", "on-site", "hybrid"

## Scraping sources
- RemoteOK        → free JSON API at remoteok.com/api, no auth needed
- Himalayas       → requests + BeautifulSoup
- Wellfound       → Playwright (JS-heavy)
- We Work Remotely → BeautifulSoup

Deduplication: skip any company domain already in DB.
No artificial cap on scraping — pull everything, filter hard.

## config.yml structure
scraping:
  sources:
    remoteok: true
    himalayas: true
    wellfound: true
    we_work_remotely: true

qualification:
  min_score: 7
  min_countries: 3
  max_headcount: 150
  stack_keywords: [java, spring boot, react, typescript, docker, postgresql]
  exclude_keywords: ["us only", "must be in", "on-site", "hybrid"]

outreach:
  max_drafts_per_day: 3
  max_emails_per_week: 15
  followup_after_days: 7
  language: english

targets:
  regions: [worldwide, us, canada, europe]

## API keys (all in .env, never hardcoded)
ANTHROPIC_API_KEY   → qualifier (claude-haiku-4-5-20251001) + mailer (claude-sonnet-4-6)
HUNTER_API_KEY      → finder.py email lookup
GMAIL_CLIENT_ID     → mailer.py sending
GMAIL_CLIENT_SECRET → mailer.py sending
GMAIL_REFRESH_TOKEN → mailer.py sending
FLASK_SECRET_KEY    → dashboard sessions

## Claude API usage
qualifier.py → claude-haiku-4-5-20251001 (fast + cheap, scores companies)
mailer.py    → claude-sonnet-4-6 (best quality for email drafts)

Qualifier prompt returns JSON:
{
  "score": 8,
  "remote_friendly": true,
  "stack_match": 0.75,
  "reasoning": "...",
  "disqualify": false
}

Email prompt returns plain text with subject line.
Always in English unless config says French.

## Pipeline flow (main.py, runs daily at 8am)
1. scraper.py   → fetch new companies, dedup, insert to DB
2. qualifier.py → score each new company, drop score < min_score
3. finder.py    → Hunter.io lookup for qualified companies
4. mailer.py    → generate drafts (status='draft'), max per config
5. followup.py  → check emails sent 7 days ago, queue follow-ups

Also: /run-pipeline endpoint on Flask triggers manually.

## Email structure Claude must follow
Subject: specific, references company or role

Line 1 - hook: something specific about them
         (recent funding, product launch, stack, blog post)
Line 2 - relevance: "I build with Java/Spring Boot + React,
         matches your [specific thing]"
Line 3 - proof: ONE concrete thing from Mohammed's background
Line 4 - small ask: "Would a 15-min call make sense?"

Max 150 words. No fluff. No "I am passionate about technology".
Sign off: Mohammed Bouziyani | mb.bouziyani@gmail.com |
          linkedin.com/in/mohammed-bouziyani

## Mohammed's profile (for email generation)
Name: Mohammed Bouziyani
Stack: Java, Spring Boot, React/TypeScript, Node.js,
       Docker, PostgreSQL, REST APIs, JWT, JUnit
Experience:
  - Networia (Feb-May 2025): task management app,
    Spring Boot + React, JWT, Docker, SonarQube
  - Vision Business Consulting (Mar-Sep 2024): web + mobile
    apps for BASF + Fondation Mohammed VI, 20% perf improvement
  - Networia (Jun-Aug 2023): medical practice management system
  - FSSM Marrakech (May-Jul 2022): HR application React + PHP
Project: e-commerce platform, Spring Boot + React + PostgreSQL + Docker
Education: Computer Science & Information Systems Engineering,
           Université Privée de Marrakech (2024)
Languages: Arabic (native), French (C1), English (B2)
Location: Morocco, open to remote worldwide

## Phase 2 — Hermes Agent (after core pipeline works)
Added as third container in docker-compose.yml.
Shares /data/jobs.db volume (read + write status column).
Responsibilities:
  - Telegram bot: notify Mohammed when drafts ready
  - Mobile approval: "approve 1 2" → updates DB status
  - Daily summary: scraped / qualified / sent / replies
  - Weekly digest: Sunday 9am stats message
Skill file: /hermes/skills/job_agent.py
Uses same ANTHROPIC_API_KEY from .env.
Does NOT replace Flask dashboard — Flask stays as backup UI.

## Build order (one Claude Code session per phase)
Session 1: SQLite schema + scraper.py + docker-compose.yml skeleton
Session 2: qualifier.py + Claude API integration + config.py
Session 3: Flask dashboard (pipeline.html + settings.html)
Session 4: finder.py + Hunter.io integration
Session 5: mailer.py + review.html + Gmail OAuth
Session 6: followup.py + APScheduler wiring + main.py
Session 7: Hermes Agent container + Telegram bot skill

## Rules for every session
- All secrets in .env, never hardcoded anywhere
- SQLite only, no external DB server needed
- config.yml is the single place to tune behaviour
- Every module reads config via config.py load_config()
- Flask dashboard writes back to config.yml on settings save
- Deduplication always checked before any insert
- Claude API calls always wrapped in try/except
- Log everything with Python logging module (not print)
- Docker images must be linux/amd64 compatible (DigitalOcean Frankfurt)

## VPS details
IP: 64.226.118.230
OS: Ubuntu 24.04 LTS
RAM: 4GB
SSD: 90GB
Region: Frankfurt (FRA1)
User: root
Project path: /opt/job-agent
