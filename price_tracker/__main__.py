"""CLI entry point for Amazon price tracker."""

from __future__ import annotations

import logging
import sys
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import click
from apscheduler.schedulers.blocking import BlockingScheduler

from price_tracker.settings import Settings
from price_tracker.tracker import PriceTracker

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    log_dir = Path("data")
    log_dir.mkdir(parents=True, exist_ok=True)
    # Her saat başı yeni dosya; en fazla 1 eski saat tutulur
    file_handler = TimedRotatingFileHandler(
        log_dir / "tracker.log",
        when="H",
        interval=1,
        backupCount=1,
        encoding="utf-8",
        utc=False,
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


_setup_logging()
logger = logging.getLogger("price_tracker")


@click.group()
def cli() -> None:
    """Amazon kategori fiyat takipçisi — düşüşleri Telegram / Email ile bildirir."""


@cli.command("run")
@click.option(
    "--headed",
    is_flag=True,
    help="Chrome'u görünür modda aç (debug için).",
)
def run_once(headed: bool) -> None:
    """Kategorileri bir kez tara ve fiyat düşüşlerini bildir."""
    settings = Settings()
    if headed:
        settings.headless = False
    tracker = PriceTracker(settings)
    tracker.run_once()


@cli.command("schedule")
@click.option(
    "--interval",
    default=None,
    type=int,
    help="Tarama aralığı (dakika). Varsayılan: .env SCAN_INTERVAL_MINUTES",
)
def schedule(interval: int | None) -> None:
    """Belirli aralıklarla sürekli tara (Ctrl+C ile durdur)."""
    from price_tracker.telegram_bot import TelegramHistoryBot

    settings = Settings()
    minutes = interval or settings.scan_interval_minutes
    tracker = PriceTracker(settings)
    bot = TelegramHistoryBot(settings, tracker.store)
    bot.start()

    logger.info("İlk tarama başlıyor...")
    tracker.run_once()

    scheduler = BlockingScheduler()
    scheduler.add_job(
        tracker.run_once,
        "interval",
        minutes=minutes,
        id="amazon_price_scan",
        max_instances=1,
        coalesce=True,
    )
    logger.info("Zamanlayıcı aktif: her %d dakikada bir tarama.", minutes)

    def _hourly_log_cleanup() -> None:
        """Eski rotasyon dosyalarını ve şişmiş log'u temizle."""
        log_dir = Path("data")
        current = log_dir / "tracker.log"
        # Saatlik rotasyon dışı kalmış yedekleri sil
        for path in log_dir.glob("tracker.log.*"):
            try:
                # TimedRotating yedeği: tracker.log.YYYY-MM-DD_HH
                # backupCount=1 zaten çoğu siliyor; ekstra temizlik
                if time.time() - path.stat().st_mtime > 2 * 3600:
                    path.unlink(missing_ok=True)
                    logger.info("Eski log silindi: %s", path.name)
            except OSError:
                logger.exception("Log silinemedi: %s", path)

        # Aktif log 5 MB'ı aştıysa truncate et (saat ortası şişme)
        try:
            if current.exists() and current.stat().st_size > 5_000_000:
                current.write_text("", encoding="utf-8")
                logger.info("tracker.log truncate edildi (5MB üstü).")
        except OSError:
            logger.exception("tracker.log truncate başarısız")

    scheduler.add_job(
        _hourly_log_cleanup,
        "cron",
        minute=0,
        id="hourly_log_cleanup",
        max_instances=1,
        coalesce=True,
    )
    logger.info("Saatlik log temizliği aktif.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Zamanlayıcı durduruldu.")
        bot.stop()
        sys.exit(0)


@cli.command("test-notify")
def test_notify() -> None:
    """Telegram / Email ayarlarını test mesajı ile doğrula."""
    from price_tracker.models import PriceChange, Product
    from price_tracker.notifier import Notifier

    settings = Settings()
    notifier = Notifier(settings)
    sample = PriceChange(
        product=Product(
            title="Tuya Uyumlu Dahili Sıcaklık ve Nem Sensörlü WiFi IR Kumanda (WAK-02P)",
            url="https://www.amazon.com.tr/dp/B0FD3QVBV9",
            asin_code="B0FD3QVBV9",
            image_url="https://m.media-amazon.com/images/I/31qImo8TTnL._AC_UL320_.jpg",
            price=650.00,
        ),
        old_price=710.00,
        new_price=650.00,
        change_amount=60.0,
        change_percent=8.5,
        direction="down",
        category="Ev Otomasyonu",
    )
    sample_up = PriceChange(
        product=Product(
            title="Test Ürün — Fiyat Artışı",
            url="https://www.amazon.com.tr/dp/B0FD3QVBV9",
            asin_code="TESTUP0010",
            image_url="https://m.media-amazon.com/images/I/31qImo8TTnL._AC_UL320_.jpg",
            price=800.00,
        ),
        old_price=710.00,
        new_price=800.00,
        change_amount=90.0,
        change_percent=12.7,
        direction="up",
        category="Ev Otomasyonu",
    )
    notifier.notify_changes([sample, sample_up])
    click.echo("Test bildirimi gönderildi (ayarlar açıksa).")


if __name__ == "__main__":
    cli()
