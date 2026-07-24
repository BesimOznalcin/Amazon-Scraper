"""Telegram bot: geçmiş, kategori istatistikleri, kategori ekleme."""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

import requests

from price_tracker import config_io
from price_tracker.settings import Settings
from price_tracker.storage import PriceStore, ProductRecord

logger = logging.getLogger(__name__)
TR_TZ = ZoneInfo("Europe/Istanbul")
ASIN_RE = re.compile(r"\b([A-Z0-9]{10})\b", re.IGNORECASE)

HELP_TEXT = (
    "<b>Amazon Fiyat Botu</b>\n\n"
    "<b>Komutlar</b>\n"
    "• /kategoriler — kategori bazında ürün sayıları\n"
    "• /ekle İsim | url_veya_arama | max_sayfa\n"
    "• /aktif İsim — kategoriyi aç\n"
    "• /pasif İsim — kategoriyi kapat\n"
    "• /gecmis ASIN — fiyat geçmişi\n"
    "• ASIN yaz — fiyat geçmişi\n"
    "• /help — bu yardım\n\n"
    "<b>Örnekler</b>\n"
    "<code>/ekle Kamp Çadırı | kamp çadırı | 2</code>\n"
    "<code>/pasif Su</code>\n"
    "<code>/aktif Playstation 5</code>"
)


class TelegramHistoryBot:
    """Long-poll Telegram komut işleyici."""

    def __init__(self, settings: Settings, store: PriceStore | None = None) -> None:
        self.settings = settings
        self.store = store or PriceStore(settings.db_path)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._offset = 0

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.telegram_enabled
            and self.settings.telegram_bot_token
            and self.settings.telegram_chat_id
        )

    def start(self) -> None:
        if not self.enabled:
            logger.info("Telegram botu kapalı (ayarlar eksik).")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="telegram-bot",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Telegram botu aktif (/kategoriler, /ekle, /aktif, /pasif, /gecmis)."
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _poll_loop(self) -> None:
        token = self.settings.telegram_bot_token
        while not self._stop.is_set():
            try:
                # Her döngüde güncel chat/token için settings yenile
                self.settings = Settings()
                token = self.settings.telegram_bot_token
                resp = requests.get(
                    f"https://api.telegram.org/bot{token}/getUpdates",
                    params={
                        "timeout": 25,
                        "offset": self._offset,
                        "allowed_updates": ["message"],
                    },
                    timeout=35,
                )
                resp.raise_for_status()
                updates = resp.json().get("result", [])
                for update in updates:
                    self._offset = update["update_id"] + 1
                    try:
                        self._handle_update(update)
                    except Exception:
                        logger.exception("Telegram komut işlenemedi")
                        try:
                            msg = update.get("message") or {}
                            cid = str((msg.get("chat") or {}).get("id", ""))
                            if cid:
                                self._reply(
                                    cid,
                                    "Komut işlenirken hata oluştu. Log'a bakıldı.",
                                )
                        except Exception:
                            pass
            except requests.RequestException:
                logger.exception("Telegram bot poll hatası")
                time.sleep(5)
            except Exception:
                logger.exception("Telegram bot beklenmeyen hata")
                time.sleep(5)

    def _handle_update(self, update: dict) -> None:
        message = update.get("message") or {}
        text = (message.get("text") or "").strip()
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        if not text or not chat_id:
            return

        if chat_id != str(self.settings.telegram_chat_id):
            return

        lower = text.lower().strip()
        cmd = lower.split()[0].split("@")[0] if lower.startswith("/") else ""

        if cmd in ("/start", "/help", "/yardim") or lower in ("yardim", "yardım"):
            self._reply(chat_id, HELP_TEXT)
            return

        if cmd in ("/kategoriler", "/kategori", "/stats", "/istatistik"):
            # /kategori İsim → tek kategori
            rest = text.split(maxsplit=1)
            if cmd == "/kategori" and len(rest) >= 2:
                self._reply(chat_id, self._format_one_category(rest[1].strip()))
            else:
                self._reply(chat_id, self._format_category_stats())
            return

        if cmd == "/ekle":
            self._reply(chat_id, self._handle_add_category(text))
            return

        if cmd in ("/aktif", "/ac", "/enable", "/pasif", "/kapat", "/disable"):
            self._reply(chat_id, self._handle_toggle_category(text, cmd))
            return

        if cmd in ("/gecmis", "/history", "/fiyat"):
            asin = self._extract_asin(text)
            if not asin:
                self._reply(chat_id, "ASIN gerekli. Örnek: /gecmis B0FD3QVBV9")
                return
            record = self.store.get_product_with_history(asin)
            self._reply(chat_id, self._format_history(asin, record))
            return

        # Düz ASIN
        asin = self._extract_asin(text)
        if asin and re.fullmatch(r"[A-Z0-9]{10}", text.strip().upper()):
            record = self.store.get_product_with_history(asin)
            self._reply(chat_id, self._format_history(asin, record))

    def _handle_add_category(self, text: str) -> str:
        # /ekle İsim | url_veya_arama | max_pages
        body = text.split(maxsplit=1)
        if len(body) < 2:
            return (
                "Kullanım:\n"
                "<code>/ekle İsim | url_veya_arama | max_sayfa</code>\n\n"
                "Örnek:\n"
                "<code>/ekle Kamp Çadırı | kamp çadırı | 2</code>"
            )

        parts = [p.strip() for p in body[1].split("|")]
        if len(parts) < 2:
            return (
                "Eksik bilgi. Format:\n"
                "<code>/ekle İsim | url_veya_arama | max_sayfa</code>"
            )

        name = parts[0]
        url_or_query = parts[1]
        max_pages = 2
        if len(parts) >= 3 and parts[2]:
            try:
                max_pages = int(parts[2])
            except ValueError:
                return "max_sayfa sayı olmalı. Örnek: <code>| 2</code>"

        try:
            category = config_io.add_category(
                self.settings.config_path,
                name=name,
                url_or_query=url_or_query,
                max_pages=max_pages,
                domain=self.settings.amazon_domain,
            )
        except ValueError as exc:
            return f"Eklenemedi: {escape(str(exc))}"
        except Exception as exc:
            logger.exception("Kategori ekleme hatası")
            return f"Hata: {escape(str(exc))}"

        return (
            "<b>Kategori eklendi</b>\n"
            f"Ad: <b>{escape(category.name)}</b>\n"
            f"max_pages: <b>{category.max_pages}</b>\n"
            f"URL: {escape(category.url)}\n\n"
            "Bir sonraki taramada otomatik dahil edilir."
        )

    def _handle_toggle_category(self, text: str, cmd: str) -> str:
        enabled = cmd in ("/aktif", "/ac", "/enable")
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            action = "aktif" if enabled else "pasif"
            return (
                f"Kullanım: <code>/{action} Kategori Adı</code>\n"
                f"Örnek: <code>/{action} Su</code>\n"
                "Liste: /kategoriler"
            )
        name = parts[1].strip()
        try:
            category = config_io.set_category_enabled(
                self.settings.config_path, name, enabled
            )
        except ValueError as exc:
            return f"İşlem yapılamadı: {escape(str(exc))}"
        except Exception as exc:
            logger.exception("Kategori aktif/pasif hatası")
            return f"Hata: {escape(str(exc))}"

        state = "aktif" if category.enabled else "pasif"
        return (
            f"<b>{escape(category.name)}</b> artık <b>{state}</b>.\n"
            "Değişiklik sonraki taramada geçerli."
        )

    def _format_category_stats(self) -> str:
        config = config_io.load_app_config(self.settings.config_path)
        db_counts = {
            name: (total, priced)
            for name, total, priced in self.store.count_products_by_category()
        }
        total_all, priced_all = self.store.total_product_counts()

        lines = [
            "<b>Kategori ürün sayıları</b>",
            f"Toplam DB: <b>{total_all}</b> ürün "
            f"(fiyatlı: <b>{priced_all}</b>)",
            "",
            "<b>Takip edilen kategoriler</b>",
        ]

        if not config.categories:
            lines.append("config.yaml içinde kategori yok.")
        else:
            for cat in config.categories:
                total, priced = db_counts.get(cat.name, (0, 0))
                status = "açık" if cat.enabled else "kapalı"
                pages = cat.max_pages if cat.max_pages is not None else "-"
                lines.append(
                    f"• <b>{escape(cat.name)}</b> [{status}, max_pages={pages}]\n"
                    f"  DB: {total} ürün / {priced} fiyatlı"
                )

        # Config'de olmayan ama DB'de kalan kategoriler
        configured = {c.name for c in config.categories}
        orphans = [
            (name, total, priced)
            for name, (total, priced) in db_counts.items()
            if name not in configured and name != "(kategori yok)"
        ]
        if orphans:
            lines.append("")
            lines.append("<b>DB'de kalan (config dışı)</b>")
            for name, total, priced in orphans:
                lines.append(
                    f"• {escape(name)}: {total} ürün / {priced} fiyatlı"
                )

        none_cat = db_counts.get("(kategori yok)")
        if none_cat:
            lines.append("")
            lines.append(
                f"Kategorisiz: {none_cat[0]} ürün / {none_cat[1]} fiyatlı"
            )

        lines.append("")
        lines.append("Eklemek için: <code>/ekle İsim | arama | 2</code>")
        return "\n".join(lines)[:4000]

    def _format_one_category(self, name: str) -> str:
        total, priced = self.store.count_products_for_category(name)
        config = config_io.load_app_config(self.settings.config_path)
        conf = next(
            (c for c in config.categories if c.name.lower() == name.lower()),
            None,
        )
        lines = [f"<b>{escape(name)}</b>"]
        if conf:
            lines.append(f"Durum: {'açık' if conf.enabled else 'kapalı'}")
            lines.append(f"max_pages: {conf.max_pages}")
            lines.append(f"URL: {escape(conf.url)}")
        else:
            lines.append("config.yaml içinde tanımlı değil.")
        lines.append(f"DB ürün: <b>{total}</b>")
        lines.append(f"Fiyatlı: <b>{priced}</b>")
        return "\n".join(lines)

    def _extract_asin(self, text: str) -> str | None:
        parts = text.strip().split()
        if parts and parts[0].lower().split("@")[0] in (
            "/gecmis",
            "/history",
            "/fiyat",
        ):
            if len(parts) >= 2:
                candidate = parts[1].upper()
                if re.fullmatch(r"[A-Z0-9]{10}", candidate):
                    return candidate
            return None
        match = ASIN_RE.search(text.upper())
        return match.group(1) if match else None

    def _format_price(self, value: float | None) -> str:
        if value is None:
            return "-"
        formatted = f"{value:,.2f}"
        if self.settings.amazon_domain == "com.tr":
            formatted = formatted.replace(",", "X").replace(".", ",").replace("X", ".")
            return f"{formatted} TL"
        return formatted

    def _format_dt(self, iso_value: str) -> str:
        try:
            dt = datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
            return dt.astimezone(TR_TZ).strftime("%d.%m.%Y %H:%M")
        except ValueError:
            return iso_value

    def _format_history(self, asin: str, record: ProductRecord | None) -> str:
        if record is None:
            return (
                f"ASIN bulunamadı: {asin}\n"
                "Bu ürün henüz taranmamış veya veritabanında yok."
            )

        lines = [
            f"<b>{escape(record.title[:120])}</b>",
            f"ASIN: <code>{record.asin}</code>",
        ]
        if record.category:
            lines.append(f"Kategori: {escape(record.category)}")
        lines.append(f"Son fiyat: <b>{self._format_price(record.last_price)}</b>")
        lines.append(f"En düşük: {self._format_price(record.lowest_price)}")
        lines.append(f"Güncelleme: {self._format_dt(record.updated_at)}")
        lines.append(f'<a href="{escape(record.url)}">Ürüne git</a>')
        lines.append("")
        lines.append("<b>Fiyat geçmişi</b>")

        if not record.history:
            lines.append("Henüz fiyat geçmişi kaydı yok.")
        else:
            for point in record.history:
                when = self._format_dt(point.recorded_at)
                price = self._format_price(point.price)
                if point.direction == "down" and point.old_price is not None:
                    detail = (
                        f"{self._format_price(point.old_price)} → {price} "
                        f"(-%{point.change_percent:.1f})"
                    )
                elif point.direction == "up" and point.old_price is not None:
                    detail = (
                        f"{self._format_price(point.old_price)} → {price} "
                        f"(+%{point.change_percent:.1f})"
                    )
                elif point.direction == "new":
                    detail = f"İlk kayıt: {price}"
                else:
                    detail = price
                lines.append(f"• {when}: {detail}")

        return "\n".join(lines)[:4000]

    def _reply(self, chat_id: str, text: str) -> None:
        token = self.settings.telegram_bot_token
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=30,
            )
            resp.raise_for_status()
        except requests.RequestException:
            logger.exception("Telegram yanıtı gönderilemedi")
