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
- `companies.domain` nullable — Clearbit fills it at scrape time; finder.py skips companies where domain IS NULL or ''
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
- mailer.py, followup.py — Session 6+
- review.html (email approve/reject UI) — Session 6

---

## Session 5 status (completed 2026-06-05)

### What was built
- `agent/finder.py` — Hunter.io domain-search integration; finds best contact per qualified company
- `agent/db.py` — added `get_qualified_without_contacts(conn)` and `insert_contact(conn, ...)` helpers
- `agent/main.py` — pipeline now runs scraper → qualifier → finder in sequence
- `dashboard/app.py` — added `/run-finder` POST route for manual trigger; added `HUNTER_API` +
  `_tier()` + `_best_contact()` + `_hunter_search()` helpers (self-contained; agent/finder.py
  is in a separate container and cannot be imported)
- `dashboard/templates/pipeline.html` — "Run Finder" button (top-right); success/error banner
  shown after finder runs (via `?msg=` query param redirect)
- `dashboard/requirements.txt` — added `requests>=2.32`

### What works
- `finder.run(conn, cfg)`: queries all `qualified=1` companies that have a `domain` and no row
  yet in `contacts`; for each calls Hunter.io domain-search (limit=10); picks best contact via
  `_best_contact()` and writes to `contacts` table
- Role priority is headcount-aware (three tier lists — see table below);
  within same tier, prefers personal-type addresses over generic, then sorts by confidence desc
- `verified = confidence > 70` — stored as `INTEGER 0/1` in `contacts.verified`
- `HUNTER_API_KEY` checked at startup — logs error and returns early if missing
- 1 s sleep between Hunter.io calls (free plan: 25 req/month; polite pace)
- `/run-finder` in dashboard: identical Hunter.io + ranking logic; runs synchronously and
  redirects to `/pipeline?msg=Finder done — N contacts found (M processed, K skipped)`
- `Run Finder` button on pipeline page triggers the route; success banner shows on redirect

### Role priority tiers (headcount-aware)

`_tiers_for(headcount)` selects the active list; `NULL` headcount → LARGE.
Within same tier: personal-type email > generic; then confidence descending.
`verified = confidence > 70` (strictly greater, not >=)

**Small — headcount < 30** (reach the technical decision-maker directly)
```
Tier 0: cto, chief technology officer, chief technical officer
Tier 1: co-founder, cofounder, co founder
Tier 2: head of engineering, vp of engineering, vp engineering, director of engineering
Tier 3: (any other / no position — last resort)
```

**Mid — headcount 30–80** (hiring manager / lead is the right entry point; CTO is fallback)
```
Tier 0: engineering manager, technical manager, tech manager
Tier 1: tech lead, technical lead, lead engineer, lead developer, staff engineer
Tier 2: technical recruiter, tech recruiter
Tier 3: cto, chief technology officer, chief technical officer
Tier 4: (any other / no position — last resort)
```

**Large — headcount > 80 or NULL** (go through recruiting / people ops first)
```
Tier 0: technical recruiter, tech recruiter
Tier 1: talent acquisition, talent partner, talent sourcer, talent specialist
Tier 2: people operations, people ops, hr manager, human resources
Tier 3: engineering manager, technical manager, tech manager
Tier 4: (any other / no position — last resort)
```

### DB helpers added in Session 5
- `get_qualified_without_contact(conn)` — `qualified=1 AND domain IS NOT NULL AND id NOT IN contacts`
- `insert_contact(conn, company_id, name, role, email, verified)` — `source='hunter'` hardcoded

### Design decisions
- Self-contained Hunter.io logic in `dashboard/app.py` (duplicates ~40 lines from finder.py)
  because dashboard and agent containers don't share a filesystem — importing agent code is
  impossible without a shared volume or HTTP API
- `/run-finder`, `/run-hermes-finder`, `/run-all` all run synchronously in the Flask request —
  acceptable for a personal tool with small batches; a background thread would add complexity
  with no benefit at this scale
- `/run-hermes-finder`: POSTs to `http://host.docker.internal:8765/find-emails` (tiny Flask server
  on the VPS host that runs hermes); timeout=180s, shows first 300 chars of response in the banner;
  on `ConnectionError` returns "Hermes finder not available — trigger manually via Telegram" verbatim
  (no "Hermes error —" prefix, so the banner reads cleanly); other errors prefixed with "Hermes error"
- `/run-all`: runs Hunter finder → Hermes finder → Mailer in sequence via `_do_hunter_finder`,
  `_do_hermes_finder`, and `_run_mailer_for`; collects one-line results from each step and joins
  them with ` | ` in the redirect banner
- `_do_hunter_finder(conn, api_key)` and `_do_hermes_finder()` extracted as helpers so both the
  individual routes and `/run-all` can call them without duplicating logic
- `_best_contact` sorts by: (1) personal > generic type, (2) role tier, (3) confidence desc
  so the best personal email for the most senior role always wins

### Bug fixed post-Session 5
- `get_qualified_without_contact` used `NOT IN (SELECT DISTINCT company_id FROM contacts)`.
  If `contacts.company_id` contains any NULL (possible when the table was created before the
  `NOT NULL` constraint was tightened via `CREATE TABLE IF NOT EXISTS`), every `NOT IN` comparison
  evaluates to NULL/unknown → zero rows returned even with a full DB.
  Fixed: replaced with `NOT EXISTS (SELECT 1 FROM contacts WHERE contacts.company_id = companies.id)`,
  which is NULL-safe. Same fix applied to the identical inline query in `dashboard/app.py`.
- Also added `AND domain != ''` guard — Clearbit can store an empty string for companies it
  can't resolve; these passed `IS NOT NULL` but broke Hunter.io's domain search.
- Added explicit debug log: `get_qualified_without_contact returned N companies` logged before
  the loop so the fetch count is visible even when Hunter.io calls are skipped.

### Not yet built (do NOT re-implement)
- Hermes Agent / Telegram bot — Session 8

---

## Session 7 status (completed 2026-06-05)

### What was built
- `agent/followup.py` — checks sent emails ≥ N days old with no reply, generates short
  follow-up via `claude-haiku-4-5-20251001`, inserts new draft into `emails` table
- `agent/db.py` — added `get_emails_needing_followup(conn, days)` helper
- `agent/main.py` — replaced `while True / time.sleep(86400)` with APScheduler
  `BlockingScheduler`; pipeline runs immediately on startup then daily at 08:00 UTC
- `dashboard/app.py` — added `/run-followup` POST route + `_build_followup_prompt()` helper
- `dashboard/templates/review.html` — added "Run Followup" button (yellow) alongside "Run Mailer"

### What works
- `followup.run(conn, cfg)`: queries `emails` where `status='sent'`, `sent_at <= now - N days`,
  and `NOT EXISTS` any other email for the same company (catches drafts, sent followups, and
  skipped followups — prevents generating a second followup if one already exists as a draft);
  generates 2-3 sentence follow-up via haiku; inserts as new `emails` row with `status='draft'`
- Follow-up prompt: references the original subject, asks if they had a chance to look, ≤ 50 words
  body, same sign-off, subject always `Re: {original_subject}`; bans filler phrases explicitly
- `followup_after_days` read from `config.yml outreach.followup_after_days` (currently 7)
- `/run-followup`: identical inline logic to `followup.py`; redirects to /review with result banner
- APScheduler `BlockingScheduler(timezone='UTC')` with `cron` trigger at `hour=10, minute=0`;
  `misfire_grace_time=3600` so if the container was down at 10am it still runs within 1 hour
- Startup run: `run_pipeline()` called directly before `scheduler.start()` so the first cycle
  never waits until 10am; subsequent runs are always at 10:00 UTC

### Design decisions
- Haiku (not Sonnet) for follow-ups: 2-3 sentences need no creativity or research — haiku is
  faster, cheaper, and more than capable of short templated text
- `NOT EXISTS (SELECT 1 FROM emails e2 WHERE e2.company_id = e.company_id AND e2.id != e.id)`
  as the "already followed up" check: catches any state of follow-up (draft / sent / skipped)
  without needing a separate `followups` table or a `type` column; the existing `followups` table
  remains in the schema but is unused — left for future multi-stage followup logic
- Follow-ups stored as new rows in `emails` table (not in `followups` table) — keeps the review
  queue unified; "Re: " subject prefix makes them visually distinct in review.html
- `misfire_grace_time=3600` on the APScheduler job: container restarts or brief downtime around
  8am UTC won't silently miss a day's run
- No `time` import needed in main.py any more — removed

### DB helper added in Session 7
- `get_emails_needing_followup(conn, days=7)` — `status='sent' AND sent_at <= datetime('now', '-N days') AND NOT EXISTS (other email for same company)`; returns full company + contact join

---

## Session 6 status (completed 2026-06-05)

### What was built
- `agent/mailer.py` — Claude sonnet-4-6 draft generation; reads `max_drafts_per_day` from config
- `agent/db.py` — added `get_companies_for_draft(conn)` and `insert_email_draft(conn, ...)` helpers
- `agent/main.py` — pipeline is now scrape → qualify → find contacts → draft emails
- `dashboard/app.py` — added `/review`, `/approve/<id>`, `/skip/<id>`, `/run-mailer`; added
  `_build_mailer_prompt()`, `_run_mailer_for()`, `_gmail_service()`, `_send_via_gmail()` helpers;
  also switched pipeline.html's `NOT IN` subqueries to `NOT EXISTS` (same NULL-safety fix as db.py)
- `dashboard/templates/review.html` — NEW: draft email cards with contact info, subject, body,
  Approve & Send / Skip buttons; "Run Mailer" button at top
- `dashboard/templates/base.html` — "Review" nav link added; review card CSS + email-body
  preformatted block + `.btn-approve` (green) + `.btn-skip` (red) styles
- `dashboard/requirements.txt` — added `anthropic>=0.32`, `google-auth>=2.0`,
  `google-api-python-client>=2.0`

### What works
- `mailer.run(conn, cfg)`: queries companies with contact but no email (`NOT EXISTS` on emails);
  calls `claude-sonnet-4-6` with a structured prompt; parses JSON `{subject, body}`; inserts
  rows into `emails` with `status='draft'`; stops at `max_drafts_per_day` cap; 1 s between calls
- Draft prompt structure: Hook → Relevance → Proof → Ask → Sign-off; ≤ 150 words body;
  Mohammed's full profile hardcoded in prompt; subject references the company specifically
- `/review` shows all `status='draft'` emails joined with company + contact
- `/approve/<id>`: fetches draft, calls `_send_via_gmail()`, updates `status='sent'` + `sent_at`
- `/skip/<id>`: updates `status='skipped'`, redirects back to /review
- `/run-mailer`: self-contained inline version of the draft logic (identical prompt);
  reads `max_drafts_per_day` from `/data/config.yml`; redirects to /review with result banner
- Gmail sending: `google.oauth2.credentials.Credentials(token=None, refresh_token=..., ...)` +
  `build('gmail', 'v1', ...)` + `MIMEText(body, 'plain', 'utf-8')` → base64 → `messages.send()`
  `cache_discovery=False` prevents discovery-doc caching issues in Docker

### DB helpers added in Session 6
- `get_companies_for_draft(conn)` — `qualified=1 AND has contact AND NOT EXISTS in emails`
- `insert_email_draft(conn, company_id, contact_id, subject, body)` — `status='draft'`, returns id

### Design decisions
- `_gmail_service()` and `_send_via_gmail()` live in `dashboard/app.py` only (not agent) — sending
  is a human-triggered action, not part of the automated pipeline
- `_build_mailer_prompt()` duplicated in `agent/mailer.py` and `dashboard/app.py` — two-container
  architecture makes sharing code impossible; kept in sync manually
- Gmail credentials imported lazily inside the helper functions so the dashboard still starts if
  `google-auth` fails to import (e.g. during local dev without the package installed)
- `cache_discovery=False` passed to `build()` — prevents `googleapiclient` from writing a
  discovery cache file to a potentially read-only filesystem inside Docker
- `msg` query-param pattern for flash messages (consistent with existing pipeline/finder)
- Approve button sends immediately with no confirmation — intentional; user already reviewed body

### Not yet built (do NOT re-implement)
- followup.py — Session 7
- APScheduler — Session 7

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
│   ├── scraper.py        # RemoteOK + WWR + Remote.co + Jobspresso + Wellfound + Clearbit
│   ├── qualifier.py      # Claude haiku scoring, writes remote_score + qualified
│   ├── finder.py         # Hunter.io domain search → contacts table
│   ├── mailer.py         # Claude sonnet draft generation → emails table (status='draft')
│   ├── followup.py       # follow-up draft generation via Claude haiku → emails table
│   └── config.py         # loads /data/config.yml
├── dashboard/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app.py            # Flask: pipeline / companies / stats / settings / health + force_qualify
│   └── templates/
│       ├── base.html     # dark CSS, nav (4+5-col kanban, filter tabs, btn-sm, red badge)
│       ├── pipeline.html # 5-column kanban (All Scraped + Discovered + Qualified + Email Found + Contacted)
│       ├── companies.html # full company browser with filters + force-qualify button
│       ├── review.html   # draft email cards — Approve & Send / Skip buttons
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
- `domain`: NULL until Clearbit finds it; finder.py skips companies where domain IS NULL

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
- **Remotive** — free JSON API at `https://remotive.com/api/remote-jobs`, no auth;
  returns `jobs[]` with `company_name`, `company_url`, `tags`, `description`; source key `'remotive'`
- **Jobspresso** — BeautifulSoup HTML scraper on `https://jobspresso.co/remote-work/`;
  same WP Job Manager layout; source key `'jobspresso'`
- **Wellfound** — Playwright (headless Chromium) on `https://wellfound.com/jobs?remote=true`;
  scrolls 3×, extracts company names from `a[href*="/company/"]` anchors; **disabled by default**
  (set `wellfound: true` in config.yml to enable); source key `'wellfound'`

Deduplication: `UNIQUE(name, source)` — checked before every insert.
Domain discovery: Clearbit Autocomplete immediately after insert.
Himalayas: dropped (persistent 403).

## config.yml (current live values)
```yaml
scraping:
  sources:
    remoteok: true
    we_work_remotely: true
    remotive: true
    jobspresso: true
    wellfound: false

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

## Gmail OAuth setup (one-time, run locally)
```
1. Go to Google Cloud Console → APIs & Services → Credentials
2. Create OAuth 2.0 Client ID (Desktop app) → download as credentials.json → place in project root
3. pip install google-auth-oauthlib
4. python get_token.py          ← opens browser, authorises, prints all three values
5. Copy GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN into .env
6. credentials.json is in .gitignore — never commit it
```
- `get_token.py` uses `InstalledAppFlow.from_client_secrets_file` + `run_local_server(port=0)`
- Scope: `https://www.googleapis.com/auth/gmail.send` (send-only, no read access)
- `credentials.json` and `get_token.py` are project-root only; neither goes into a container

## Pipeline flow (Sessions 1–7 implemented — fully automated)
```
1. scraper.py   → fetch new companies, dedup, Clearbit domain, insert to DB  ✓
2. qualifier.py → score unqualified companies via Claude haiku, update DB     ✓
3. finder.py    → Hunter.io lookup for qualified companies with a domain      ✓
4. mailer.py    → generate drafts via Claude sonnet, status='draft'           ✓
5. followup.py  → check emails sent 7 days ago with no reply, draft followup ✓

Scheduling: APScheduler BlockingScheduler — runs immediately on startup, then daily at 10:00 UTC
```

## Email structure Claude must follow
```
Subject: specific, references company or role

Line 1 — hook: category-specific style (see table below)
Line 2 — relevance: category-specific angle
Line 3 — proof: chosen from three rotating proof points (see below)
Line 4 — ask: "Would a 15-min call make sense?"

Max 150 words. No fluff. No "I am passionate about technology".
Sign-off: Mohammed Bouziyani | mb.bouziyani@gmail.com | linkedin.com/in/mohammed-bouziyani
```

### Three rotating proof points

Claude chooses one per email based on the category preference below.

| Label | Description |
|---|---|
| **A** | Networia medical system (Jun–Aug 2023): Spring Boot + React medical practice management, JWT, production-deployed — healthcare domain, Java depth |
| **B** | Vision Business Consulting (Mar–Sep 2024): web + mobile apps for enterprise clients in manufacturing and healthcare sectors, 20% performance gain — enterprise scale, French-language context |
| **C** | Job Search Agent (2025): multi-agent AI pipeline, Claude API, Docker Compose on DigitalOcean (4 GB VPS), APScheduler, Flask — LLM integration + self-managed production infra |

### Company category classification (mailer.py + dashboard/app.py)

Each company is classified before drafting. Classification uses description + stack keywords
(and `.fr` domain suffix for FRENCH_STARTUP). Priority order: AI_COMPANY → DEVOPS_CLOUD →
FRENCH_STARTUP → JAVA_SPRING → NODEJS_PYTHON → GENERAL_TECH.

| Category | Detection keywords | Hook style | Proof preference |
|---|---|---|---|
| `AI_COMPANY` | machine learning, llm, langchain, openai, generative ai, neural network, nlp, embedding, vector database | Specific technical observation about their AI approach — model choice, RAG arch, inference pattern. Not generic praise. | Prefer C, fall back to A |
| `DEVOPS_CLOUD` | kubernetes, terraform, ci/cd, platform engineering, sre, infrastructure as code, gitops, helm | Infrastructure/scale observation — name the tool and say something about how they use it. | Prefer C, fall back to A |
| `FRENCH_STARTUP` | france, french, paris, lyon, francophone + `.fr` domain | First sentence in French (specific, not generic), rest in English. | Prefer B, fall back to A |
| `JAVA_SPRING` | spring boot, spring framework, hibernate, micronaut, quarkus, kotlin | Name a specific technical detail from their stack — a Spring module, architectural pattern, or library. | Prefer A, fall back to B |
| `NODEJS_PYTHON` | node.js, nodejs, nestjs, django, fastapi, flask, python backend | Versatility angle — acknowledge their stack, show cross-language fluency through concrete work (not "fast learner"). | Prefer C, fall back to A |
| `GENERAL_TECH` | (fallback) | Something specific from their description — product decision, stack choice, open-source work. | Best fit from A/B/C |

Both `agent/mailer.py` and `dashboard/app.py` contain `_PROOFS` + full classifier + angle dict (kept in sync manually — two-container architecture makes imports impossible).

## Mohammed's profile (for email generation)
```
Name:   Mohammed Bouziyani
Stack:  Java, Spring Boot, React/TypeScript, Node.js, Docker, PostgreSQL, REST APIs, JWT, JUnit
Location: Morocco — open to remote worldwide

Experience:
  Networia (Feb–May 2025):             task management app, Spring Boot + React, JWT, Docker, SonarQube
  Vision Business Consulting (Mar–Sep 2024): web + mobile apps for enterprise clients in manufacturing and healthcare sectors, 20% perf gain
  Networia (Jun–Aug 2023):             medical practice management system
  FSSM Marrakech (May–Jul 2022):       HR application, React + PHP

Projects:
  Job Search Agent: multi-agent pipeline with Claude API (Anthropic), APScheduler, SQLite, Flask dashboard,
    Docker Compose — deployed on DigitalOcean (4 GB VPS, Ubuntu 24.04) — end-to-end system in production
  E-commerce platform: Spring Boot + React + PostgreSQL + Docker

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
Session 5: finder.py + Hunter.io integration                                  ✓ done
Session 6: mailer.py + review.html + Gmail send + /approve /skip /run-mailer  ✓ done
Session 7: followup.py + APScheduler (10:00 UTC) + /run-followup              ✓ done
Session 8: Remote.co + Jobspresso + Wellfound scrapers added to scraper.py    ✓ done
Session 8b: mailer.py — company category classifier + per-category email angles ✓ done
Session 8c: dashboard — /run-hermes-finder + /run-all + "Find with Hermes" button ✓ done
Session 9: Hermes Agent container + Telegram bot
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
