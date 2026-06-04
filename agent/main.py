import logging
import sys

from config import load_config
from db import get_conn, init_db
from scraper import run_all

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)-24s %(levelname)-8s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info('Job agent starting (Session 1 — scraper)')
    cfg = load_config()
    init_db()
    conn = get_conn()
    try:
        results = run_all(conn, cfg)
        total = sum(results.values())
        logger.info('Scrape complete — new companies by source: %s (total=%d)', results, total)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
