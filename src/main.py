"""main.py — orchestrates a single monitor run.

Flow (matches the project's required main flow exactly):

    Load Config -> Load State -> Download HTML -> Parse -> Detect Change
    -> Notify -> Save State -> Exit

``run_once()`` is the entire flow as a single, testable function that
returns a process exit code — it is what GitHub Actions (or any other
one-shot cron runner) calls directly. ``main()`` additionally supports an
optional ``--schedule`` flag for local, continuous use via APScheduler;
this is a convenience layer on top of ``run_once()`` and requires no
changes to any other module to add, satisfying the "move to GitHub Actions
without touching core code" requirement.
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

from src.config import ConfigError, Settings, load_config
from src.detector import detect_changes
from src.models import StoredState
from src.notifier import TelegramNotifier
from src.parser import ParserError, parse_search_results
from src.scraper import Scraper
from src.storage import StorageError, load_state, save_state
from src.utils import BANGKOK_TZ, configure_logging

logger = logging.getLogger(__name__)

EXIT_SUCCESS = 0
EXIT_CONFIG_ERROR = 1
EXIT_FETCH_ERROR = 2
EXIT_PARSE_ERROR = 3
EXIT_STORAGE_ERROR = 4


def run_once(settings: Settings | None = None) -> int:
    """Run the full monitor flow exactly once. Returns a process exit code
    (0 on success, non-zero on the first unrecoverable failure)."""
    logger.info("[bold]เริ่มตรวจสอบสต็อกสินค้า Pokemon TCG MA6[/bold]", extra={"markup": True})

    if settings is None:
        try:
            settings = load_config()
        except ConfigError as exc:
            logger.error("โหลด Config ไม่สำเร็จ: %s", exc)
            return EXIT_CONFIG_ERROR

    state = load_state(settings.state_file)
    logger.info("โหลด state เดิมสำเร็จ: พบสินค้าที่บันทึกไว้ %d รายการ", len(state.products))

    scraper = Scraper(timeout_seconds=settings.request_timeout)
    fetch_result = scraper.fetch_html(settings.target_url)
    if not fetch_result.success or fetch_result.html is None:
        logger.error("ดาวน์โหลดหน้าเว็บไม่สำเร็จ: %s", fetch_result.error)
        return EXIT_FETCH_ERROR
    logger.info("ดาวน์โหลด HTML สำเร็จ (status=%s)", fetch_result.status_code)

    try:
        products = parse_search_results(fetch_result.html, source_url=settings.target_url)
    except ParserError as exc:
        logger.error(
            "โครงสร้างหน้าเว็บเปลี่ยนแปลงหรือแยกวิเคราะห์ไม่สำเร็จ (HTML_CHANGED): %s", exc
        )
        return EXIT_PARSE_ERROR
    logger.info("แยกวิเคราะห์ HTML สำเร็จ: พบสินค้า %d รายการในหน้าค้นหา", len(products))

    events = detect_changes(products, state.products)
    if not events:
        logger.info("ไม่พบการเปลี่ยนแปลงตั้งแต่การตรวจสอบครั้งก่อน")
    else:
        logger.warning("พบการเปลี่ยนแปลง %d รายการ กำลังส่งการแจ้งเตือน...", len(events))
        notifier = TelegramNotifier(
            bot_token=settings.bot_token,
            chat_id=settings.chat_id,
            timeout_seconds=settings.request_timeout,
        )
        sent_count = notifier.send_events(events)
        if sent_count < len(events):
            logger.warning("ส่งการแจ้งเตือนสำเร็จ %d/%d รายการ (บางรายการล้มเหลว)", sent_count, len(events))
        else:
            logger.info("ส่งการแจ้งเตือนสำเร็จครบทุกรายการ (%d รายการ)", sent_count)

    new_state = StoredState(
        products={product.id: product for product in products},
        last_checked_at=datetime.now(timezone.utc),
    )
    try:
        save_state(settings.state_file, new_state)
    except StorageError as exc:
        logger.error("บันทึก state ไม่สำเร็จ: %s", exc)
        return EXIT_STORAGE_ERROR

    logger.info("[bold green]ตรวจสอบเสร็จสิ้น[/bold green]", extra={"markup": True})
    return EXIT_SUCCESS


def _run_scheduled(cron_expression: str) -> int:
    """Run `run_once` repeatedly on a cron schedule until interrupted.
    Local/dev convenience only — GitHub Actions should call `run_once`
    (i.e. run with no --schedule flag) directly from its own cron trigger.
    """
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BlockingScheduler(timezone=str(BANGKOK_TZ))
    trigger = CronTrigger.from_crontab(cron_expression)
    scheduler.add_job(run_once, trigger=trigger)

    logger.info("เริ่มทำงานแบบตั้งเวลาด้วย cron: %s (กด Ctrl+C เพื่อหยุด)", cron_expression)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("หยุดการทำงานแบบตั้งเวลา")
    return EXIT_SUCCESS


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pokemon TCG MA6 Stock Monitor")
    parser.add_argument(
        "--schedule",
        metavar="CRON",
        default=None,
        help=(
            "Run continuously on a 5-field cron expression (e.g. '*/30 * * * *') "
            "using APScheduler. Omit this flag to run once and exit — this is "
            "the mode GitHub Actions should use."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _parse_args(argv)
    if args.schedule:
        return _run_scheduled(args.schedule)
    return run_once()


if __name__ == "__main__":
    raise SystemExit(main())
