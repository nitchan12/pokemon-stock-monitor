"""main.py — orchestrates a single monitor run.

Flow::

    Load Config -> Load State -> for each product page: Download -> Parse
    -> Detect (in stock?) -> Notify -> Save State -> Exit

``run_once()`` is the entire flow as one testable function returning a
process exit code — it is what GitHub Actions (or any other one-shot cron
runner) calls directly. ``main()`` additionally supports an optional
``--schedule`` flag for local continuous use via APScheduler; that is a
convenience layer on top of ``run_once()`` requiring no changes to any
other module, which is what keeps the project portable between GitHub
Actions and a long-running local process.

Partial-failure policy: if one of the three product pages fails to
download or parse, the run logs it and continues with the others rather
than aborting. Missing one page must never prevent an alert for a
different page that just came back in stock. The run only reports failure
if *every* page failed.
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timezone

from src.config import ConfigError, Settings, load_config
from src.detector import detect_in_stock
from src.models import Product, StoredState
from src.notifier import TelegramNotifier
from src.parser import ParserError, parse_product_page
from src.scraper import Scraper
from src.storage import StorageError, load_state, save_state
from src.utils import BANGKOK_TZ, configure_logging

logger = logging.getLogger(__name__)

EXIT_SUCCESS = 0
EXIT_CONFIG_ERROR = 1
EXIT_FETCH_ERROR = 2
EXIT_STORAGE_ERROR = 4


def run_once(settings: Settings | None = None) -> int:
    """Run the full monitor flow exactly once. Returns a process exit code."""
    logger.info("[bold]เริ่มตรวจสอบสต็อกสินค้า Pokemon TCG MA6[/bold]", extra={"markup": True})

    if settings is None:
        try:
            settings = load_config()
        except ConfigError as exc:
            logger.error("โหลด Config ไม่สำเร็จ: %s", exc)
            return EXIT_CONFIG_ERROR

    state = _load_state_safely(settings)
    logger.info("โหลด state เดิมสำเร็จ: มีสินค้าที่บันทึกไว้ %d รายการ", len(state.products))

    products = _collect_products(settings)
    if not products:
        logger.error("ไม่สามารถอ่านหน้าสินค้าได้เลยแม้แต่รายการเดียว")
        return EXIT_FETCH_ERROR

    result = detect_in_stock(
        products=products,
        previous_state=state.products,
        now=datetime.now(timezone.utc),
        max_notify_count=settings.max_notify_count,
        repeat_interval_minutes=settings.repeat_interval_minutes,
    )

    if not result.events:
        logger.info("ยังไม่มีสินค้าพร้อมจำหน่าย (หรืออยู่ในช่วงพักการแจ้งเตือนซ้ำ)")
    else:
        logger.warning("พบสินค้าพร้อมจำหน่าย %d รายการ กำลังแจ้งเตือน...", len(result.events))
        notifier = TelegramNotifier(
            bot_token=settings.bot_token,
            chat_id=settings.chat_id,
            timeout_seconds=settings.request_timeout,
            max_notify_count=settings.max_notify_count,
        )
        sent_count = notifier.send_events(result.events)
        if sent_count < len(result.events):
            logger.warning(
                "ส่งการแจ้งเตือนสำเร็จ %d/%d รายการ (บางรายการล้มเหลว)",
                sent_count,
                len(result.events),
            )
        else:
            logger.info("ส่งการแจ้งเตือนสำเร็จครบทุกรายการ (%d รายการ)", sent_count)

    try:
        save_state(
            settings.state_file,
            StoredState(products=result.new_state, last_checked_at=datetime.now(timezone.utc)),
        )
    except StorageError as exc:
        logger.error("บันทึก state ไม่สำเร็จ: %s", exc)
        return EXIT_STORAGE_ERROR

    logger.info("[bold green]ตรวจสอบเสร็จสิ้น[/bold green]", extra={"markup": True})
    return EXIT_SUCCESS


def _load_state_safely(settings: Settings) -> StoredState:
    """Load persisted state, degrading to empty state if it is unusable.

    A state file that cannot be read — corrupt, or written by an older
    version with an incompatible schema — must not take the monitor down.
    The cost of starting fresh is bounded and safe: out-of-stock products
    are simply re-recorded, and an in-stock product alerts again, which is
    the behavior we want anyway. Crashing instead would mean no alerts at
    all until someone notices, which is strictly worse.
    """
    try:
        return load_state(settings.state_file)
    except StorageError as exc:
        logger.error(
            "อ่าน state เดิมไม่ได้ (%s) — เริ่มต้นใหม่จาก state ว่าง "
            "อาจมีการแจ้งเตือนซ้ำหนึ่งรอบหากสินค้ามีของอยู่",
            exc,
        )
        return StoredState()


def _collect_products(settings: Settings) -> list[Product]:
    """Download and parse every configured product page.

    Failures on individual pages are logged and skipped so that one broken
    page cannot suppress an alert for the others.
    """
    scraper = Scraper(timeout_seconds=settings.request_timeout)
    products: list[Product] = []

    for index, url in enumerate(settings.product_urls):
        if index > 0 and settings.request_delay_seconds > 0:
            time.sleep(settings.request_delay_seconds)

        fetch_result = scraper.fetch_html(url)
        if not fetch_result.success or fetch_result.html is None:
            logger.error("ดาวน์โหลดหน้าสินค้าไม่สำเร็จ (%s): %s", url, fetch_result.error)
            continue

        try:
            product = parse_product_page(fetch_result.html, url)
        except ParserError as exc:
            logger.error(
                "โครงสร้างหน้าสินค้าเปลี่ยนแปลงหรือแยกวิเคราะห์ไม่สำเร็จ (%s): %s", url, exc
            )
            continue

        logger.info("ตรวจสอบแล้ว: %s -> %s", product.name[:50], product.availability.value)
        products.append(product)

    return products


def _run_scheduled(cron_expression: str) -> int:
    """Run `run_once` repeatedly on a cron schedule until interrupted.

    Local/dev convenience only — GitHub Actions should call `run_once`
    (i.e. run with no --schedule flag) from its own cron trigger.
    """
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BlockingScheduler(timezone=str(BANGKOK_TZ))
    scheduler.add_job(run_once, trigger=CronTrigger.from_crontab(cron_expression))

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
            "Run continuously on a 5-field cron expression (e.g. '*/2 * * * *') "
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
