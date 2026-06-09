import logging
import sys

from apscheduler.schedulers.blocking import BlockingScheduler

from config import load_config
from db import get_conn, init_db
from finder import run as find_contacts
from followup import run as send_followups
from mailer import run as draft_emails
from qualifier import run as qualify
from reply_monitor import run as check_replies
from scraper import run_all

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)-24s %(levelname)-8s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def run_pipeline() -> None:
    logger.info('Pipeline starting')
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

        mailer_results = draft_emails(conn, cfg)
        logger.info('Mailer complete — %s', mailer_results)

        followup_results = send_followups(conn, cfg)
        logger.info('Followup complete — %s', followup_results)

        reply_results = check_replies()
        logger.info('Reply monitor complete — %s', reply_results)
    finally:
        conn.close()
    logger.info('Pipeline complete')


def main() -> None:
    logger.info('Job agent starting — running pipeline now, then daily at 08:00 UTC')

    # Run immediately on startup so the first cycle doesn't wait until 8am.
    run_pipeline()

    scheduler = BlockingScheduler(timezone='UTC')
    scheduler.add_job(
        run_pipeline,
        trigger='cron',
        hour=10,
        minute=0,
        misfire_grace_time=3600,  # still run if container was down at 10am, up to 1h late
    )
    logger.info('Scheduler started — next run at 10:00 UTC')

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info('Scheduler stopped')


if __name__ == '__main__':
    main()
