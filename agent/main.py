import logging
import sys
import time

from config import load_config
from db import get_conn, init_db
from finder import run as find_contacts
from qualifier import run as qualify
from scraper import run_all

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)-24s %(levelname)-8s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

SLEEP_SECONDS = 86400  # replaced by APScheduler in Session 6


def run_pipeline() -> None:
    cfg = load_config()
    init_db()
    conn = get_conn()
    try:
        scrape_results = run_all(conn, cfg)
        logger.info(
            'Scrape complete — %s (total=%d)',
            scrape_results, sum(scrape_results.values()),
        )
        qual_results = qualify(conn, cfg)
        logger.info('Qualification complete — %s', qual_results)

        finder_results = find_contacts(conn, cfg)
        logger.info('Finder complete — %s', finder_results)
    finally:
        conn.close()


def main() -> None:
    logger.info('Job agent starting')
    while True:
        run_pipeline()
        logger.info('Sleeping %ds until next run', SLEEP_SECONDS)
        time.sleep(SLEEP_SECONDS)


if __name__ == '__main__':
    main()
