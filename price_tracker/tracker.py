"""Orchestrates scraping, price comparison, and notifications."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from price_tracker.models import (
    AppConfig,
    CategoryConfig,
    CategoryScanResult,
    PriceChange,
    ScanSummary,
)
from price_tracker.notifier import Notifier
from price_tracker.scraper import AmazonScraper
from price_tracker.settings import Settings
from price_tracker.storage import PriceChangeKind, PriceStore

logger = logging.getLogger(__name__)
TR_TZ = ZoneInfo("Europe/Istanbul")


class PriceTracker:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.store = PriceStore(self.settings.db_path)
        self.notifier = Notifier(self.settings)
        self._notify_lock = threading.Lock()

    def reload_settings(self) -> None:
        """Her taramada .env değişikliklerini al."""
        self.settings = Settings()
        self.notifier = Notifier(self.settings)
        # db_path değişmediyse aynı store; değiştiyse yenile
        if str(self.store.db_path) != self.settings.db_path:
            self.store = PriceStore(self.settings.db_path)

    def load_config(self) -> AppConfig:
        path = Path(self.settings.config_path)
        if not path.exists():
            raise FileNotFoundError(
                f"config.yaml bulunamadı: {path.resolve()}. "
                "Örnek dosyayı kopyalayıp kategori URL'lerini ekle."
            )
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return AppConfig.model_validate(raw)

    def load_categories(self) -> list[CategoryConfig]:
        return [c for c in self.load_config().categories if c.enabled]

    def _scan_category(
        self, category: CategoryConfig
    ) -> tuple[CategoryScanResult, list[PriceChange]]:
        """Tek kategoriyi tara (thread-safe; kendi Chrome sürücüsünü açar)."""
        drop_threshold = (
            category.min_drop_percent
            if category.min_drop_percent is not None
            else self.settings.min_drop_percent
        )
        increase_threshold = (
            category.min_increase_percent
            if category.min_increase_percent is not None
            else self.settings.min_increase_percent
        )
        max_pages = (
            category.max_pages
            if category.max_pages is not None
            else self.settings.max_pages
        )

        cat_result = CategoryScanResult(name=category.name)
        alerts: list[PriceChange] = []
        scraper = AmazonScraper(
            domain=self.settings.amazon_domain,
            headless=self.settings.headless,
        )

        logger.info("Kategori başlıyor: %s", category.name)
        try:
            products = scraper.scrape_page(category.url, max_pages=max_pages)
        except Exception as exc:
            logger.exception("Kategori taranamadı: %s", category.name)
            cat_result.error = str(exc)[:120]
            return cat_result, alerts

        cat_result.product_count = len(products)
        cat_result.pages_hint = "tümü" if max_pages <= 0 else f"max {max_pages} sayfa"

        for product in products:
            product.category = category.name
            result = self.store.upsert_product(product)
            if result.kind == PriceChangeKind.NEW:
                cat_result.new_count += 1
            elif result.kind == PriceChangeKind.UNCHANGED:
                cat_result.unchanged_count += 1
            elif result.kind == PriceChangeKind.INCREASED:
                cat_result.increased_count += 1
            elif result.kind == PriceChangeKind.DECREASED:
                cat_result.decreased_count += 1
            elif result.kind == PriceChangeKind.NO_PRICE:
                cat_result.no_price_count += 1

            change = result.change
            if change is None:
                continue
            change.category = category.name

            should_alert = False
            if change.is_drop and change.change_percent >= drop_threshold:
                should_alert = True
                cat_result.drop_count += 1
                logger.info(
                    "Düşüş [%s]: %s | %.2f → %.2f (%%%.1f) — anında bildiriliyor",
                    category.name,
                    product.title[:50],
                    change.old_price,
                    change.new_price,
                    change.change_percent,
                )
            elif (
                not change.is_drop
                and self.settings.notify_price_increases
                and change.change_percent >= increase_threshold
            ):
                should_alert = True
                cat_result.increase_alert_count += 1
                logger.info(
                    "Artış [%s]: %s | %.2f → %.2f (%%%.1f) — anında bildiriliyor",
                    category.name,
                    product.title[:50],
                    change.old_price,
                    change.new_price,
                    change.change_percent,
                )

            if should_alert:
                alerts.append(change)
                with self._notify_lock:
                    self.notifier.notify_change(change)

        logger.info(
            "Kategori bitti: %s (%d ürün)",
            category.name,
            cat_result.product_count,
        )
        return cat_result, alerts

    def run_once(self) -> list[PriceChange]:
        self.reload_settings()
        app_config = self.load_config()
        categories = [c for c in app_config.categories if c.enabled]
        # config.yaml öncelikli; yoksa .env
        status_enabled = app_config.status_notifications
        started = datetime.now(TR_TZ)
        if not categories:
            logger.warning("Aktif kategori yok. config.yaml'ı kontrol et.")
            finished = datetime.now(TR_TZ)
            if status_enabled:
                self.notifier.notify_scan_status(
                    ScanSummary(
                        started_at=started.strftime("%d.%m.%Y %H:%M:%S"),
                        finished_at=finished.strftime("%d.%m.%Y %H:%M:%S"),
                        duration_seconds=int((finished - started).total_seconds()),
                    )
                )
            return []

        workers = max(1, min(self.settings.scan_workers, len(categories)))
        logger.info(
            "%d kategori paralel taranacak (workers=%d)",
            len(categories),
            workers,
        )

        alert_changes: list[PriceChange] = []
        results: list[CategoryScanResult] = []
        # Kategori sırasını korumak için isim -> sonuç
        by_name: dict[str, CategoryScanResult] = {}

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self._scan_category, category): category
                for category in categories
            }
            for future in as_completed(futures):
                category = futures[future]
                try:
                    cat_result, alerts = future.result()
                except Exception as exc:
                    logger.exception("Kategori thread hatası: %s", category.name)
                    cat_result = CategoryScanResult(
                        name=category.name, error=str(exc)[:120]
                    )
                    alerts = []
                by_name[category.name] = cat_result
                alert_changes.extend(alerts)

        results = [by_name[c.name] for c in categories if c.name in by_name]

        finished = datetime.now(TR_TZ)
        drop_alerts = sum(1 for c in alert_changes if c.is_drop)
        increase_alerts = sum(1 for c in alert_changes if not c.is_drop)
        summary = ScanSummary(
            categories=results,
            drop_count=drop_alerts,
            increase_alert_count=increase_alerts,
            new_count=sum(r.new_count for r in results),
            unchanged_count=sum(r.unchanged_count for r in results),
            increased_count=sum(r.increased_count for r in results),
            decreased_count=sum(r.decreased_count for r in results),
            no_price_count=sum(r.no_price_count for r in results),
            started_at=started.strftime("%d.%m.%Y %H:%M:%S"),
            finished_at=finished.strftime("%d.%m.%Y %H:%M:%S"),
            duration_seconds=int((finished - started).total_seconds()),
        )
        if status_enabled:
            self.notifier.notify_scan_status(summary)

        logger.info(
            "Tarama bitti. %d ürün | yeni=%d aynı=%d artış=%d düşüş=%d | "
            "bildirim düşüş=%d artış=%d | süre=%s",
            summary.total_products,
            summary.new_count,
            summary.unchanged_count,
            summary.increased_count,
            summary.decreased_count,
            drop_alerts,
            increase_alerts,
            summary.duration_label,
        )
        return alert_changes
