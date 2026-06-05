# CLAUDE.md — Job Search Agent

## Session 1 status (completed 2026-06-05)

### What was built
- `agent/db.py` — SQLite schema init: companies, contacts, emails, followups tables
- `agent/config.py` — loads `data/config.yml` via `load_config()`
- `agent/scraper.py` — RemoteOK (JSON API) + We Work Remotely (RSS) scrapers with deduplication
- `agent/main.py` — entry point: infinite loop, runs pipeline then `time.sleep(86400)`
- `docker-compose.yml` — agent + dashboard containers, `/data` shared volume

### What works
- RemoteOK: hits JSON API, fixes encoding with `resp.encoding='utf-8'` + `_fix_encoding()` per name,
  skips legal notice (no `company` key), deduplicates by name, stores `domain=None` initially,
  then immediately calls Clearbit Autocomplete to discover real domain and updates the row
- We Work Remotely: fetches RSS at `https://weworkremotely.com/remote-jobs.rss`, parses with
  BeautifulSoup xml parser, extracts company from title ("… at Company Name"), same Clearbit lookup
- Clearbit Autocomplete: `https://autocomplete.clearbit.com/v1/companies/suggest?query=NAME`
  free, no key, returns `[{domain, name, logo}, …]` — take `results[0].domain` if present
- Deduplication: `company_exists(conn, name, source)` checks `UNIQUE(name, source)` in DB
- `main.py` loops forever — run pipeline, sleep 24 h, repeat (APScheduler replaces in Session 6)

### DB schema decisions
- `companies.domain` nullable — Clearbit fills it at scrape time; NULLs handled by finder.py (Session 4)
- Unique constraint is `UNIQUE(name, source)` — not on `domain`
- **Schema changed on existing DB**: delete `/data/jobs.db` and restart

### Confirmed RemoteOK API fields (verified via curl)
`slug, id, epoch, date, company, company_logo, position, tags, description, location,`
`apply_url, salary_min, salary_max, logo, url`
Note: `url` is a remoteok.com job page URL — DO NOT use for domain/dedup. Use `company` name.

### Bugs fixed this session
- `scraper.py` used undefined `_HEADERS` — silently returned 0. Fixed.
- `main.py` exited after one run. Fixed: `while True` + `time.sleep(86400)`.
- RemoteOK `url` field → remoteok.com, dedup collapsed all records to 1. Fixed: use company name.
- RemoteOK UTF-8 mojibake — fixed with `resp.encoding='utf-8'` + `_fix_encoding()` per name.
- Himalayas returned 403 — replaced entirely with We Work Remotely RSS scraper.
- `docker-compose.yml` had obsolete `version: '3.8'` key — removed.
- `extract_domain()` was dead code after Clearbit replaced URL-based domain extraction — removed.
- `config.yml` had stale `himalayas: true` key and `we_work_remotely: false` — fixed.

---

## Session 2 status (completed 2026-06-05)

### What was built
- `agent/qualifier.py` — scores every unqualified company via Claude API, updates DB
- `agent/db.py` — added `qualified INTEGER DEFAULT NULL` column + migration + two new helpers
- `agent/main.py` — pipeline now runs scraper → qualifier in sequence

### What works
- `qualifier.run(conn, cfg)` fetches all `qualified IS NULL` companies, scores each with
  `claude-haiku-4-5-20251001`, parses JSON, writes `remote_score` + `qualified` back to DB
- Score prompt includes company name, domain, description, stack, headcount; framing tells
  Claude to be generous — score 5+ = worth investigating, 0-2 only for obviously non-remote
- `_unwrap_json()` strips markdown fences Claude sometimes wraps responses in
- `min_score` from `config.yml qualification.min_score` (currently **4** for tuning; raise to 7 later)
- ANTHROPIC_API_KEY checked at runtime — logs error and returns early if missing
- 0.5 s sleep between Claude calls to stay within haiku rate limits
- Migration: `ALTER TABLE companies ADD COLUMN qualified INTEGER DEFAULT NULL` runs on startup,
  silently skipped if column already exists

### DB changes in Session 2
- `companies.qualified` — NULL = not scored, 1 = qualified (score ≥ min_score), 0 = rejected
- `companies.remote_score` — written by qualifier (scraper always inserts 0)
- New helpers: `get_unqualified_companies(conn)`, `update_company_qualification(conn, id, score, qualified)`

### Qualifier prompt JSON response format
```json
{"score": 7, "remote_friendly": true, "stack_match": 0.8, "reasoning": "one sentence", "disqualify": false}
```

---

## Session 3 status (completed 2026-06-05)

### What was built
- `dashboard/app.py` — Flask app: /, /pipeline, /stats, /settings, /health
- `dashboard/templates/base.html` — dark minimal CSS (no external deps), nav with active states
- `dashboard/templates/pipeline.html` — 4-column kanban
- `dashboard/templates/settings.html` — YAML textarea, validates before saving
- `dashboard/templates/stats.html` — 8-number headline grid + top qualified table + by-source table

### Kanban stage definitions
| Column | SQL condition |
|--------|---------------|
| Discovered | `qualified IS NULL` |
| Qualified | `qualified = 1` AND no row in `contacts` |
| Email Found | `qualified = 1` AND has contact AND no sent email |
| Contacted | has email with `status IN ('sent','replied')` |

### Design decisions
- Pure CSS in base.html, zero external libraries — works offline, no CDN dependency
- DB opened per-request (`sqlite3.connect`) — single-user dashboard, no pool needed
- Settings saves only after `yaml.safe_load` validates — bad YAML shows inline error
- All DB errors caught and shown as inline banners, dashboard never hard-crashes

### Not yet built (do NOT re-implement)
- finder.py, mailer.py, followup.py — Session 5+
- review.html (email approve/reject UI) — Session 5

---

## Session 4 status (completed 2026-06-05)

### What was built
- `dashboard/app.py` — added `/companies` route (filter: all/qualified/rejected/no_domain) and
  `/force_qualify/<id>` POST endpoint; updated `/pipeline` to pass `all_scraped`; added
  `rejection_rate` + `avg_score` to `/stats`
- `dashboard/templates/pipeline.html` — 5th column "All Scraped" showing every company in DB
  with name, source, domain badge, and colour-coded score badge (green = qualified, red = rejected)
- `dashboard/templates/stats.html` — 2 new headline stat cards: rejection rate (%) and avg score;
  grid now 10 cards in a 5-column layout; "Rejected" stat now uses red colour
- `dashboard/templates/base.html` — `.kanban-5` modifier class for 5-column kanban grid;
  `.br` red badge variant; `.btn-sm` green outlined button; `.filter-tab` pill style for
  filter tabs; stat-grid expanded to 5 columns; `stat-value.red` colour token; "Companies"
  nav link added
- `dashboard/templates/companies.html` — NEW: full company browser with filter tabs, table
  showing name/domain/score/status/source/created_at, "Force qualify" button per rejected row

### What works
- **All Scraped kanban column**: shows every company regardless of qualification, sorted newest
  first; score badge is green for qualified=1, red for qualified=0, absent for unscored
- **`/companies` route**: four filters — `all` (default), `qualified`, `rejected`, `no_domain`;
  each renders the same table template with the active filter highlighted
- **`/force_qualify/<id>`**: POST-only, sets `qualified=1` directly in DB, redirects back to
  the referring page (companies list); bypasses scorer — intended for manual overrides
- **Stats grid**: `rejection_rate` = `(rejected/total)*100` rounded to 1 dp, 0 if no companies;
  `avg_score` = `AVG(remote_score)` over rows where `qualified IS NOT NULL` (excludes unscored
  rows whose default is 0, which would skew the average)

### Design decisions
- Force-qualify only shown for `qualified=0` rows (rejected), not for pending/already-qualified
- `/force_qualify` redirects to `request.referrer` so the user stays on whatever filtered view
  they were on; falls back to `/companies` if referrer is absent
- `avg_score` excludes unscored companies (qualified IS NULL) to avoid the default `remote_score=0`
  dragging the average down before scoring runs
- 5-column kanban grid uses a `.kanban-5` modifier class so the original `.kanban` 4-column style
  is still available for any future narrow-kanban views
- stat-grid widened from 4 to 5 columns to fit the 10 stat cards in two rows of 5

### Not yet built (do NOT re-implement)
- finder.py, mailer.py, followup.py — Session 5+
- review.html (email approve/reject UI) — Session 5

---

## What this is
A proactive cold outreach system for Mohammed Bouziyani.
Junior full-stack dev (Java/Spring Boot + React), based in Morocco,
targeting remote-worldwide companies/startups.
Goal: scrape companies, qualify them, find contact emails,
generate personalised cold emails, send, track, follow up.
NOT applying to job postings — creating opportunities that don't exist yet.

## Infrastructure
- VPS: DigitalOcean 4 GB RAM / 90 GB SSD / Ubuntu 24.04 / Frankfurt
- Deployment: Docker Compose (two containers + shared volume)
- Local dev: same Docker Compose runs identically on laptop
- Phase 2: Hermes Agent added to same Compose (after core works)

## Architecture

### Containers
```
agent       → Python scheduler + all pipeline modules
dashboard   → Flask on port 5000 (review + approve emails)
```

### Shared volume
```
/data/jobs.db    → SQLite, single source of truth
/data/config.yml → pipeline settings, editable without redeployment
```

### File structure (current state)
```
job-agent/
├── docker-compose.yml
├── .env                  # never commit
├── .env.example
├── CLAUDE.md
├── agent/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py           # entry point — scrape → qualify loop, sleep 24h
│   ├── scraper.py        # RemoteOK + We Work Remotely + Clearbit domain lookup
│   ├── qualifier.py      # Claude haiku scoring, writes remote_score + qualified
│   ├── finder.py         # NOT YET BUILT — Session 4
│   ├── mailer.py         # NOT YET BUILT — Session 5
│   ├── followup.py       # NOT YET BUILT — Session 6
│   └── config.py         # loads /data/config.yml
├── dashboard/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app.py            # Flask: pipeline / companies / stats / settings / health + force_qualify
│   └── templates/
│       ├── base.html     # dark CSS, nav (4+5-col kanban, filter tabs, btn-sm, red badge)
│       ├── pipeline.html # 5-column kanban (All Scraped + Discovered + Qualified + Email Found + Contacted)
│       ├── companies.html # full company browser with filters + force-qualify button
│       ├── review.html   # NOT YET BUILT — Session 5
│       ├── settings.html # YAML editor, validates before save
│       └── stats.html    # 10-card headline grid + top qualified table + by-source table
└── data/
    ├── jobs.db
    └── config.yml
```

## Database schema (current — Sessions 1 + 2)

### companies
```
id, name, domain (nullable), website, headcount, countries_count,
stack, description, source, remote_score, qualified (nullable int),
created_at
UNIQUE(name, source)
```
- `qualified`: NULL = not scored, 1 = passed, 0 = rejected
- `domain`: NULL until Clearbit finds it; finder.py (Session 4) handles remaining NULLs

### contacts
```
id, company_id, name, role, email, source, verified (bool)
```

### emails
```
id, company_id, contact_id, subject, body,
status CHECK IN ('draft','approved','sent','replied','skipped'),
sent_at, opened_at, created_at
```

### followups
```
id, email_id, scheduled_at, sent_at, status
```

## Scraping sources (active)
- **RemoteOK** — free JSON API at `remoteok.com/api`, no auth
- **We Work Remotely** — RSS feed at `weworkremotely.com/remote-jobs.rss`, no auth

Deduplication: `UNIQUE(name, source)` — checked before every insert.
Domain discovery: Clearbit Autocomplete immediately after insert.
Himalayas: dropped (persistent 403). Wellfound: deferred (requires Playwright).

## config.yml (current live values)
```yaml
scraping:
  sources:
    remoteok: true
    wellfound: false
    we_work_remotely: true

qualification:
  min_score: 4          # temporary — raise to 7 after tuning scorer
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
```

## API keys (all in .env, never hardcoded)
```
ANTHROPIC_API_KEY   → qualifier (claude-haiku-4-5-20251001) + mailer (claude-sonnet-4-6)
HUNTER_API_KEY      → finder.py email lookup
GMAIL_CLIENT_ID     → mailer.py sending
GMAIL_CLIENT_SECRET → mailer.py sending
GMAIL_REFRESH_TOKEN → mailer.py sending
FLASK_SECRET_KEY    → dashboard sessions
```

## Pipeline flow (Sessions 1–4 implemented)
```
1. scraper.py   → fetch new companies, dedup, Clearbit domain, insert to DB  ✓
2. qualifier.py → score unqualified companies via Claude haiku, update DB     ✓
3. finder.py    → Hunter.io lookup for qualified companies with NULL domain   (Session 5)
4. mailer.py    → generate drafts via Claude sonnet, status='draft'           (Session 5)
5. followup.py  → check emails sent 7 days ago, queue follow-ups              (Session 6)
```

## Email structure Claude must follow
```
Subject: specific, references company or role

Line 1 — hook: something specific about them (funding, product, stack, blog)
Line 2 — relevance: "I build with Java/Spring Boot + React, matches your [X]"
Line 3 — proof: ONE concrete thing from Mohammed's background
Line 4 — ask: "Would a 15-min call make sense?"

Max 150 words. No fluff. No "I am passionate about technology".
Sign-off: Mohammed Bouziyani | mb.bouziyani@gmail.com | linkedin.com/in/mohammed-bouziyani
```

## Mohammed's profile (for email generation)
```
Name:   Mohammed Bouziyani
Stack:  Java, Spring Boot, React/TypeScript, Node.js, Docker, PostgreSQL, REST APIs, JWT, JUnit
Location: Morocco — open to remote worldwide

Experience:
  Networia (Feb–May 2025):             task management app, Spring Boot + React, JWT, Docker, SonarQube
  Vision Business Consulting (Mar–Sep 2024): web + mobile apps for BASF + Fondation Mohammed VI, 20% perf gain
  Networia (Jun–Aug 2023):             medical practice management system
  FSSM Marrakech (May–Jul 2022):       HR application, React + PHP

Project: e-commerce platform — Spring Boot + React + PostgreSQL + Docker
Education: Computer Science & Information Systems Engineering, Université Privée de Marrakech (2024)
Languages: Arabic (native), French (C1), English (B2)
```

## Phase 2 — Hermes Agent (after core pipeline works)
Third container in docker-compose.yml. Shares `/data/jobs.db`.
- Telegram bot: notify when drafts ready; mobile approval ("approve 1 2" → updates DB status)
- Daily summary: scraped / qualified / sent / replies
- Weekly digest: Sunday 9 am
- Skill file: `/hermes/skills/job_agent.py`
- Uses same ANTHROPIC_API_KEY. Does NOT replace Flask dashboard.

## Build order
```
Session 1: scraper.py + db.py + docker-compose skeleton                        ✓ done
Session 2: qualifier.py + Claude API + config.py                               ✓ done
Session 3: Flask dashboard (pipeline / stats / settings)                       ✓ done
Session 4: dashboard enhancements — All Scraped column, /companies, force-qualify ✓ done
Session 5: finder.py + Hunter.io integration + mailer.py + review.html + Gmail OAuth
Session 6: followup.py + APScheduler wiring
Session 7: Hermes Agent container + Telegram bot
```

## Rules for every session
- All secrets in .env, never hardcoded anywhere
- SQLite only, no external DB server needed
- config.yml is the single place to tune behaviour
- Every agent module reads config via `config.py load_config()`
- Flask dashboard writes back to config.yml on settings save
- Deduplication always checked before any insert
- Claude API calls always wrapped in try/except
- Log everything with Python logging module (not print)
- Docker images must be linux/amd64 compatible (DigitalOcean Frankfurt)

## VPS details
```
IP:      64.226.118.230
OS:      Ubuntu 24.04 LTS
RAM:     4 GB
SSD:     90 GB
Region:  Frankfurt (FRA1)
User:    root
Path:    /opt/job-agent
```
