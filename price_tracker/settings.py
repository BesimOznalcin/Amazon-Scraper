"""Application settings loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Her Settings() oluşturuluşunda .env yeniden okunsun
        env_ignore_empty=True,
    )

    amazon_domain: str = "com.tr"
    min_drop_percent: float = 5.0
    min_increase_percent: float = 5.0
    scan_interval_minutes: int = 60
    # Kategori başına max sayfa. 0 = tüm sayfalar (üst sınır 100).
    max_pages: int = 10
    notify_price_increases: bool = True
    # Aynı anda kaç kategori taransın (her biri ayrı Chrome açar)
    scan_workers: int = 3

    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # Her tarama sonrası özet durum mesajı (fiyat düşüşü olmasa da)
    status_notifications: bool = False

    # WhatsApp: callmebot (kolay) veya meta (resmi Cloud API)
    whatsapp_enabled: bool = False
    whatsapp_provider: str = "callmebot"
    # CallMeBot: ülke kodu + numara, örn. 905551234567
    whatsapp_phone: str = ""
    whatsapp_apikey: str = ""
    # Meta Cloud API
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""

    email_enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""
    email_to: str = ""

    db_path: str = "data/prices.db"
    config_path: str = "config.yaml"
    headless: bool = True
