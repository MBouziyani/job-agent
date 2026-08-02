import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path('/data/jobs.db')


def get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    conn = get_conn(db_path)
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS companies (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                domain          TEXT,
                website         TEXT,
                headcount       INTEGER,
                countries_count INTEGER,
                stack           TEXT,
                description     TEXT,
                job_title       TEXT,
                source          TEXT,
                remote_score    REAL DEFAULT 0,
                qualified       INTEGER DEFAULT NULL,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(name, source)
            );

            CREATE TABLE IF NOT EXISTS contacts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id  INTEGER NOT NULL,
                name        TEXT,
                role        TEXT,
                email       TEXT,
                source      TEXT,
                verified    INTEGER DEFAULT 0,
                FOREIGN KEY (company_id) REFERENCES companies(id)
            );

            CREATE TABLE IF NOT EXISTS emails (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id      INTEGER NOT NULL,
                contact_id      INTEGER,
                subject         TEXT,
                body            TEXT,
                status          TEXT DEFAULT 'draft'
                                CHECK(status IN ('draft','approved','sent','replied','skipped','screening','interview','offer','rejected')),
                sent_at         TIMESTAMP,
                opened_at       TIMESTAMP,
                pipeline_stage  TEXT DEFAULT 'sent',
                sequence_step   INTEGER DEFAULT 1,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (company_id) REFERENCES companies(id),
                FOREIGN KEY (contact_id) REFERENCES contacts(id)
            );

            CREATE TABLE IF NOT EXISTS followups (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                email_id     INTEGER NOT NULL,
                scheduled_at TIMESTAMP,
                sent_at      TIMESTAMP,
                status       TEXT DEFAULT 'pending',
                FOREIGN KEY (email_id) REFERENCES emails(id)
            );
        """)
    # Migration: add qualified column to databases created before Session 2
    try:
        conn.execute('ALTER TABLE companies ADD COLUMN qualified INTEGER DEFAULT NULL')
        conn.commit()
    except Exception:
        pass  # column already exists
    # Migration: add job_title column to databases created before this update
    try:
        conn.execute('ALTER TABLE companies ADD COLUMN job_title TEXT')
        conn.commit()
    except Exception:
        pass  # column already exists
    # Migration: add timezone column for smart timing
    try:
        conn.execute('ALTER TABLE companies ADD COLUMN timezone TEXT')
        conn.commit()
    except Exception:
        pass
    # Migration: add careers_url for direct applications
    try:
        conn.execute('ALTER TABLE companies ADD COLUMN careers_url TEXT')
        conn.commit()
    except Exception:
        pass
    # Migration: add applied column for direct applications tracking
    try:
        conn.execute('ALTER TABLE companies ADD COLUMN applied INTEGER DEFAULT 0')
        conn.commit()
    except Exception:
        pass
    # Migration: add pipeline_stage to emails (for DBs created before this update)
    try:
        conn.execute("ALTER TABLE emails ADD COLUMN pipeline_stage TEXT DEFAULT 'sent'")
        conn.commit()
    except Exception:
        pass
    # Migration: add sequence_step to emails
    try:
        conn.execute('ALTER TABLE emails ADD COLUMN sequence_step INTEGER DEFAULT 1')
        conn.commit()
    except Exception:
        pass
    logger.info('Database initialised at %s', db_path)
    conn.close()


def company_exists(conn: sqlite3.Connection, name: str, source: str) -> bool:
    row = conn.execute(
        'SELECT 1 FROM companies WHERE name = ? AND source = ?', (name, source)
    ).fetchone()
    return row is not None


def insert_company(conn: sqlite3.Connection, data: dict) -> int:
    cursor = conn.execute(
        """
        INSERT INTO companies
            (name, domain, website, headcount, countries_count,
             stack, description, job_title, source, remote_score)
        VALUES
            (:name, :domain, :website, :headcount, :countries_count,
             :stack, :description, :job_title, :source, :remote_score)
        """,
        data,
    )
    conn.commit()
    return cursor.lastrowid


def update_company_domain(conn: sqlite3.Connection, company_id: int, domain: str) -> None:
    conn.execute('UPDATE companies SET domain = ? WHERE id = ?', (domain, company_id))
    conn.commit()


def get_unqualified_companies(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        'SELECT * FROM companies WHERE qualified IS NULL ORDER BY created_at'
    ).fetchall()
    return [dict(row) for row in rows]


def update_company_qualification(
    conn: sqlite3.Connection,
    company_id: int,
    score: float,
    qualified: bool,
) -> None:
    conn.execute(
        'UPDATE companies SET remote_score = ?, qualified = ? WHERE id = ?',
        (score, 1 if qualified else 0, company_id),
    )
    conn.commit()


def get_qualified_without_contact(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT * FROM companies
        WHERE qualified = 1
          AND domain IS NOT NULL
          AND domain != ''
          AND NOT EXISTS (
              SELECT 1 FROM contacts WHERE contacts.company_id = companies.id
          )
        ORDER BY remote_score DESC
    """).fetchall()
    return [dict(row) for row in rows]


def insert_contact(
    conn: sqlite3.Connection,
    company_id: int,
    name: str | None,
    role: str | None,
    email: str,
    verified: bool,
) -> None:
    conn.execute(
        """
        INSERT INTO contacts (company_id, name, role, email, source, verified)
        VALUES (?, ?, ?, ?, 'hunter', ?)
        """,
        (company_id, name, role, email, 1 if verified else 0),
    )
    conn.commit()


def get_companies_for_draft(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT
            c.*,
            ct.id    AS contact_id,
            ct.name  AS contact_name,
            ct.role  AS contact_role,
            ct.email AS contact_email
        FROM companies c
        JOIN contacts ct ON ct.company_id = c.id
        WHERE c.qualified = 1
          AND ct.verified = 1
          AND NOT EXISTS (SELECT 1 FROM emails WHERE emails.company_id = c.id)
        ORDER BY c.remote_score DESC
    """).fetchall()
    return [dict(row) for row in rows]


def insert_email_draft(
    conn: sqlite3.Connection,
    company_id: int,
    contact_id: int,
    subject: str,
    body: str,
    sequence_step: int = 1,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO emails (company_id, contact_id, subject, body, status, sequence_step)
        VALUES (?, ?, ?, ?, 'draft', ?)
        """,
        (company_id, contact_id, subject, body, sequence_step),
    )
    conn.commit()
    return cursor.lastrowid


def get_emails_needing_followup(
    conn: sqlite3.Connection, days: int = 7, sequence_step: int = 2
) -> list[dict]:
    """Find sent emails needing follow-up.
    Step 2 = first followup (after `days` days).
    Step 3 = final followup (after `days`*2 days).
    Skips companies that already have a followup at this step or higher.
    """
    max_days = days * 2 if sequence_step == 3 else 999
    rows = conn.execute("""
        SELECT
            e.*,
            c.name        AS company_name,
            c.domain      AS company_domain,
            c.description AS company_description,
            c.stack       AS company_stack,
            c.job_title   AS company_job_title,
            ct.name       AS contact_name,
            ct.role       AS contact_role,
            ct.email      AS contact_email
        FROM emails e
        JOIN companies c  ON c.id  = e.company_id
        LEFT JOIN contacts ct ON ct.id = e.contact_id
        WHERE e.status = 'sent'
          AND e.sequence_step = 1
          AND e.sent_at <= datetime('now', ?)
          AND e.sent_at > datetime('now', ?)
          AND NOT EXISTS (
              SELECT 1 FROM emails e2
              WHERE e2.company_id = e.company_id
                AND e2.sequence_step >= ?
          )
        ORDER BY e.sent_at ASC
    """, (f'-{days} days', f'-{max_days} days', sequence_step)).fetchall()
    return [dict(row) for row in rows]


def update_pipeline_stage(conn: sqlite3.Connection, email_id: int, stage: str) -> None:
    conn.execute(
        'UPDATE emails SET pipeline_stage = ?, status = ? WHERE id = ?',
        (stage, stage, email_id),
    )
    conn.commit()


def get_pipeline_stats(conn: sqlite3.Connection) -> dict[str, int]:
    stages = ['draft', 'sent', 'replied', 'screening', 'interview', 'offer', 'rejected']
    stats = {}
    for stage in stages:
        row = conn.execute(
            'SELECT COUNT(*) FROM emails WHERE status = ?', (stage,)
        ).fetchone()
        stats[stage] = row[0] or 0
    return stats
