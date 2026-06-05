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
                source          TEXT,
                remote_score    REAL DEFAULT 0,
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
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id  INTEGER NOT NULL,
                contact_id  INTEGER,
                subject     TEXT,
                body        TEXT,
                status      TEXT DEFAULT 'draft'
                            CHECK(status IN ('draft','approved','sent','replied','skipped')),
                sent_at     TIMESTAMP,
                opened_at   TIMESTAMP,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
             stack, description, source, remote_score)
        VALUES
            (:name, :domain, :website, :headcount, :countries_count,
             :stack, :description, :source, :remote_score)
        """,
        data,
    )
    conn.commit()
    return cursor.lastrowid


def update_company_domain(conn: sqlite3.Connection, company_id: int, domain: str) -> None:
    conn.execute('UPDATE companies SET domain = ? WHERE id = ?', (domain, company_id))
    conn.commit()
