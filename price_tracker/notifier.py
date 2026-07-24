"""Notification helpers: Telegram + WhatsApp + Email."""

from __future__ import annotations

import html
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote

import requests

from price_tracker.models import PriceChange, ScanSummary
from price_tracker.settings import Settings

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _any_channel(self) -> bool:
        return (
            self.settings.telegram_enabled
            or self.settings.whatsapp_enabled
            or self.settings.email_enabled
        )

    def notify_change(self, change: PriceChange) -> None:
        """Tek bir fiyat değişimini anında bildir."""
        self.notify_changes([change])

    def notify_changes(self, changes: list[PriceChange]) -> None:
        if not changes:
            return

        if self.settings.telegram_enabled:
            for change in changes:
                self._send_telegram_change(change)

        if self.settings.whatsapp_enabled:
            for change in changes:
                self._send_whatsapp_change(change)

        if self.settings.email_enabled:
            drops = [c for c in changes if c.is_drop]
            rises = [c for c in changes if not c.is_drop]
            title = f"Amazon fiyat: {len(drops)} düşüş, {len(rises)} artış"
            self._send_email(
                title, self._format_text(changes), self._format_html(changes)
            )

        if not self._any_channel():
            logger.warning(
                "Hiçbir bildirim kanalı açık değil. Değişimler sadece log'a yazılıyor:\n%s",
                self._format_text(changes),
            )

    def notify_scan_status(self, summary: ScanSummary) -> None:
        """Tarama özeti (çağıran taraf config ile aç/kapa eder)."""
        html_text = self._status_html(summary)
        plain_text = self._status_plain(summary)

        sent = False
        if self.settings.telegram_enabled:
            self._send_telegram_text(html_text[:4000])
            sent = True
        if self.settings.whatsapp_enabled:
            self._send_whatsapp_text(plain_text[:3500])
            sent = True

        if not sent:
            logger.info(
                "Tarama özeti: %d kategori, %d ürün, %d düşüş / %d artış",
                len(summary.categories),
                summary.total_products,
                summary.drop_count,
                summary.increase_alert_count,
            )

    def _status_html(self, summary: ScanSummary) -> str:
        lines = [
            "<b>Tarama tamamlandı</b>",
            f"Başlangıç: {html.escape(summary.started_at or '-')}",
            f"Bitiş: {html.escape(summary.finished_at or '-')}",
            f"Süre: <b>{html.escape(summary.duration_label)}</b>",
            "",
            f"Kategori: <b>{summary.ok_categories}</b> OK"
            + (
                f" / <b>{summary.failed_categories}</b> hata"
                if summary.failed_categories
                else ""
            ),
            f"Toplam ürün: <b>{summary.total_products}</b>",
            f"Yeni ürün (ilk kez görülen): <b>{summary.new_count}</b>",
            f"Fiyatı aynı: <b>{summary.unchanged_count}</b>",
            f"Fiyat artışı (tespit): <b>{summary.increased_count}</b>",
            f"Fiyat düşüşü (tespit): <b>{summary.decreased_count}</b>",
            f"Bildirilen düşüş: <b>{summary.drop_count}</b>",
            f"Bildirilen artış: <b>{summary.increase_alert_count}</b>",
            f"Fiyatsız: <b>{summary.no_price_count}</b>",
            "",
            "<b>Kategoriler</b>",
        ]
        for cat in summary.categories:
            name = html.escape(cat.name)
            if cat.error:
                lines.append(f"• {name}: hata ({html.escape(cat.error[:80])})")
            else:
                drop_note = ""
                if cat.drop_count:
                    drop_note += f", {cat.drop_count} düşüş bildirimi"
                if cat.increase_alert_count:
                    drop_note += f", {cat.increase_alert_count} artış bildirimi"
                page_note = f" ({html.escape(cat.pages_hint)})" if cat.pages_hint else ""
                lines.append(
                    f"• {name}: {cat.product_count} ürün{page_note}"
                    f" | yeni {cat.new_count}, aynı {cat.unchanged_count}"
                    f"{drop_note}"
                )
        if summary.drop_count == 0 and summary.increase_alert_count == 0:
            lines.extend(["", "Bu turda bildirilecek fiyat değişimi yok."])
        return "\n".join(lines)

    def _status_plain(self, summary: ScanSummary) -> str:
        lines = [
            "Tarama tamamlandı",
            f"Başlangıç: {summary.started_at or '-'}",
            f"Bitiş: {summary.finished_at or '-'}",
            f"Süre: {summary.duration_label}",
            "",
            f"Kategori: {summary.ok_categories} OK"
            + (
                f" / {summary.failed_categories} hata"
                if summary.failed_categories
                else ""
            ),
            f"Toplam ürün: {summary.total_products}",
            f"Yeni ürün (ilk kez görülen): {summary.new_count}",
            f"Fiyatı aynı: {summary.unchanged_count}",
            f"Fiyat artışı (tespit): {summary.increased_count}",
            f"Fiyat düşüşü (tespit): {summary.decreased_count}",
            f"Bildirilen düşüş: {summary.drop_count}",
            f"Bildirilen artış: {summary.increase_alert_count}",
            f"Fiyatsız: {summary.no_price_count}",
            "",
            "Kategoriler:",
        ]
        for cat in summary.categories:
            if cat.error:
                lines.append(f"• {cat.name}: hata ({cat.error[:80]})")
            else:
                drop_note = ""
                if cat.drop_count:
                    drop_note += f", {cat.drop_count} düşüş bildirimi"
                if cat.increase_alert_count:
                    drop_note += f", {cat.increase_alert_count} artış bildirimi"
                page_note = f" ({cat.pages_hint})" if cat.pages_hint else ""
                lines.append(
                    f"• {cat.name}: {cat.product_count} ürün{page_note}"
                    f" | yeni {cat.new_count}, aynı {cat.unchanged_count}"
                    f"{drop_note}"
                )
        if summary.drop_count == 0 and summary.increase_alert_count == 0:
            lines.extend(["", "Bu turda bildirilecek fiyat değişimi yok."])
        return "\n".join(lines)

    def _send_telegram_text(self, text: str) -> None:
        token = self.settings.telegram_bot_token
        chat_id = self.settings.telegram_chat_id
        if not token or not chat_id:
            logger.error("Telegram token veya chat_id eksik.")
            return
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
            logger.info("Telegram durum mesajı gönderildi.")
        except requests.RequestException:
            logger.exception("Telegram durum mesajı başarısız.")

    def _format_price(self, value: float) -> str:
        formatted = f"{value:,.2f}"
        if self.settings.amazon_domain == "com.tr":
            formatted = formatted.replace(",", "X").replace(".", ",").replace("X", ".")
            return f"{formatted} TL"
        return f"{formatted}"

    def _akakce_search_query(self, title: str, max_words: int = 5) -> str:
        """Ürün adının ilk kelimelerinden Akakçe arama sorgusu üret."""
        cleaned = title.replace("|", " ").replace("-", " ")
        words = [w for w in cleaned.split() if w]
        return " ".join(words[:max_words]).strip()

    def _akakce_search_url(self, title: str) -> str:
        query = self._akakce_search_query(title)
        if not query:
            return "https://www.akakce.com/"
        return f"https://www.akakce.com/arama/?q={quote(query)}"

    def _caption_for_change(self, change: PriceChange) -> str:
        title = html.escape(change.product.title[:200])
        category = html.escape(change.category) if change.category else ""
        new_p = self._format_price(change.new_price)
        old_p = self._format_price(change.old_price)
        pct = f"%{change.change_percent:.1f}".replace(".", ",")
        link = html.escape(change.product.url)
        akakce = html.escape(self._akakce_search_url(change.product.title))
        akakce_q = html.escape(self._akakce_search_query(change.product.title))

        if change.is_drop:
            header = "Fiyat düşüşü"
            new_label = "İndirimli fiyat"
            rate_label = "İndirim oranı"
        else:
            header = "Fiyat artışı"
            new_label = "Yeni fiyat"
            rate_label = "Artış oranı"

        lines = [f"<b>{header}</b>", f"<b>{title}</b>", ""]
        if category:
            lines.append(f"Kategori: {category}")
            lines.append("")
        lines.extend(
            [
                f"{new_label}: <b>{html.escape(new_p)}</b>",
                f"Eski fiyat: <s>{html.escape(old_p)}</s>",
                f"{rate_label}: <b>{html.escape(pct)}</b>",
                "",
                f'<a href="{link}">Amazon\'da aç</a>',
                f'<a href="{akakce}">Akakçe\'de ara: {akakce_q}</a>',
            ]
        )
        return "\n".join(lines)[:1024]

    def _plain_caption_for_change(self, change: PriceChange) -> str:
        new_p = self._format_price(change.new_price)
        old_p = self._format_price(change.old_price)
        pct = f"%{change.change_percent:.1f}".replace(".", ",")
        header = "Fiyat düşüşü" if change.is_drop else "Fiyat artışı"
        lines = [header, change.product.title[:200], ""]
        if change.category:
            lines.append(f"Kategori: {change.category}")
            lines.append("")
        lines.extend(
            [
                f"Yeni fiyat: {new_p}",
                f"Eski fiyat: {old_p}",
                f"Oran: {pct}",
                "",
                f"Amazon: {change.product.url}",
                f"Akakçe: {self._akakce_search_url(change.product.title)}",
            ]
        )
        if change.product.image_url:
            lines.extend(["", f"Görsel: {change.product.image_url}"])
        return "\n".join(lines)

    def _format_text(self, changes: list[PriceChange]) -> str:
        lines: list[str] = []
        for i, c in enumerate(changes, 1):
            kind = "Düşüş" if c.is_drop else "Artış"
            lines.append(
                f"{i}. [{kind}] {c.product.title[:100]}\n"
                f"   Yeni: {self._format_price(c.new_price)}\n"
                f"   Eski: {self._format_price(c.old_price)}\n"
                f"   Oran: %{c.change_percent:.1f}\n"
                f"   Amazon: {c.product.url}\n"
                f"   Akakçe: {self._akakce_search_url(c.product.title)}\n"
            )
        return "\n".join(lines)

    def _format_html(self, changes: list[PriceChange]) -> str:
        items = []
        for c in changes:
            kind = "Düşüş" if c.is_drop else "Artış"
            img = ""
            if c.product.image_url:
                img = (
                    f'<img src="{html.escape(c.product.image_url)}" '
                    f'alt="" width="200"><br>'
                )
            akakce = html.escape(self._akakce_search_url(c.product.title))
            items.append(
                "<li>"
                f"{img}"
                f"<strong>[{kind}] {html.escape(c.product.title)}</strong><br>"
                f"Yeni: <b>{html.escape(self._format_price(c.new_price))}</b><br>"
                f"Eski: <s>{html.escape(self._format_price(c.old_price))}</s><br>"
                f"Oran: %{c.change_percent:.1f}<br>"
                f'<a href="{html.escape(c.product.url)}">Amazon\'da aç</a> · '
                f'<a href="{akakce}">Akakçe\'de ara</a>'
                "</li>"
            )
        return (
            "<html><body>"
            f"<h2>{len(changes)} üründe fiyat değişimi</h2>"
            f"<ul>{''.join(items)}</ul>"
            "</body></html>"
        )

    def _send_telegram_change(self, change: PriceChange) -> None:
        token = self.settings.telegram_bot_token
        chat_id = self.settings.telegram_chat_id
        if not token or not chat_id:
            logger.error("Telegram token veya chat_id eksik.")
            return

        caption = self._caption_for_change(change)
        image_url = change.product.image_url

        try:
            if image_url:
                resp = requests.post(
                    f"https://api.telegram.org/bot{token}/sendPhoto",
                    json={
                        "chat_id": chat_id,
                        "photo": image_url,
                        "caption": caption,
                        "parse_mode": "HTML",
                    },
                    timeout=30,
                )
                if resp.ok:
                    logger.info(
                        "Telegram foto bildirimi gönderildi: %s",
                        change.product.asin_code,
                    )
                    return
                logger.warning(
                    "sendPhoto başarısız (%s), metne düşülüyor: %s",
                    resp.status_code,
                    resp.text[:200],
                )

            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": caption,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                },
                timeout=30,
            )
            resp.raise_for_status()
            logger.info(
                "Telegram metin bildirimi gönderildi: %s", change.product.asin_code
            )
        except requests.RequestException:
            logger.exception(
                "Telegram bildirimi başarısız: %s", change.product.asin_code
            )

    # --- WhatsApp ---

    def _send_whatsapp_change(self, change: PriceChange) -> None:
        text = self._plain_caption_for_change(change)
        provider = (self.settings.whatsapp_provider or "callmebot").lower()
        if provider == "meta" and change.product.image_url:
            if self._send_whatsapp_meta_image(change.product.image_url, text[:1024]):
                return
        self._send_whatsapp_text(text)

    def _send_whatsapp_text(self, text: str) -> None:
        provider = (self.settings.whatsapp_provider or "callmebot").lower()
        if provider == "meta":
            self._send_whatsapp_meta_text(text)
        else:
            self._send_whatsapp_callmebot(text)

    def _send_whatsapp_callmebot(self, text: str) -> None:
        phone = self.settings.whatsapp_phone.strip().lstrip("+")
        apikey = self.settings.whatsapp_apikey.strip()
        if not phone or not apikey:
            logger.error("WhatsApp CallMeBot phone veya apikey eksik.")
            return
        try:
            url = (
                "https://api.callmebot.com/whatsapp.php"
                f"?phone={quote(phone)}&text={quote(text)}&apikey={quote(apikey)}"
            )
            resp = requests.get(url, timeout=60)
            if resp.ok and "ERROR" not in resp.text.upper():
                logger.info("WhatsApp (CallMeBot) mesajı gönderildi.")
            else:
                logger.error(
                    "WhatsApp CallMeBot hata: %s %s",
                    resp.status_code,
                    resp.text[:300],
                )
        except requests.RequestException:
            logger.exception("WhatsApp CallMeBot başarısız.")

    def _send_whatsapp_meta_text(self, text: str) -> None:
        token = self.settings.whatsapp_token
        phone_id = self.settings.whatsapp_phone_number_id
        to = self.settings.whatsapp_phone.strip().lstrip("+")
        if not all([token, phone_id, to]):
            logger.error("WhatsApp Meta token / phone_number_id / phone eksik.")
            return
        try:
            resp = requests.post(
                f"https://graph.facebook.com/v21.0/{phone_id}/messages",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "messaging_product": "whatsapp",
                    "to": to,
                    "type": "text",
                    "text": {"body": text[:4096]},
                },
                timeout=30,
            )
            if resp.ok:
                logger.info("WhatsApp (Meta) metin gönderildi.")
            else:
                logger.error("WhatsApp Meta hata: %s %s", resp.status_code, resp.text[:300])
        except requests.RequestException:
            logger.exception("WhatsApp Meta metin başarısız.")

    def _send_whatsapp_meta_image(self, image_url: str, caption: str) -> bool:
        token = self.settings.whatsapp_token
        phone_id = self.settings.whatsapp_phone_number_id
        to = self.settings.whatsapp_phone.strip().lstrip("+")
        if not all([token, phone_id, to]):
            return False
        try:
            resp = requests.post(
                f"https://graph.facebook.com/v21.0/{phone_id}/messages",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "messaging_product": "whatsapp",
                    "to": to,
                    "type": "image",
                    "image": {"link": image_url, "caption": caption[:1024]},
                },
                timeout=30,
            )
            if resp.ok:
                logger.info("WhatsApp (Meta) görsel gönderildi.")
                return True
            logger.warning(
                "WhatsApp Meta görsel hata: %s %s", resp.status_code, resp.text[:200]
            )
            return False
        except requests.RequestException:
            logger.exception("WhatsApp Meta görsel başarısız.")
            return False

    def _send_email(self, subject: str, text: str, html_body: str) -> None:
        s = self.settings
        if not all([s.smtp_host, s.smtp_user, s.smtp_password, s.email_to]):
            logger.error("Email SMTP ayarları eksik.")
            return

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = s.email_from or s.smtp_user
        msg["To"] = s.email_to
        msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as server:
                server.starttls()
                server.login(s.smtp_user, s.smtp_password)
                server.sendmail(msg["From"], [s.email_to], msg.as_string())
            logger.info("Email bildirimi gönderildi → %s", s.email_to)
        except Exception:
            logger.exception("Email bildirimi başarısız.")
