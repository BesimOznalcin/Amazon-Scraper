"""config.yaml okuma / yazma."""

from __future__ import annotations

import threading
from pathlib import Path
from urllib.parse import quote_plus

import yaml

from price_tracker.models import AppConfig, CategoryConfig

_lock = threading.Lock()

_HEADER = (
    "# Amazon Scraper config\n"
    "# max_pages: kategori özel sayfa limiti (yoksa .env MAX_PAGES). 0 = tüm sayfalar.\n"
    "# Telegram: /kategoriler , /ekle , /aktif İsim , /pasif İsim\n\n"
)


def load_app_config(path: str | Path) -> AppConfig:
    path = Path(path)
    if not path.exists():
        return AppConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(raw)


def _write_config(path: Path, config: AppConfig) -> None:
    data = config.model_dump(exclude_none=True)
    path.write_text(
        _HEADER + yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def save_app_config(path: str | Path, config: AppConfig) -> None:
    with _lock:
        _write_config(Path(path), config)


def build_amazon_search_url(query: str, domain: str = "com.tr") -> str:
    q = quote_plus(query.strip())
    return f"https://www.amazon.{domain.lstrip('.')}/s?k={q}"


def add_category(
    path: str | Path,
    name: str,
    url_or_query: str,
    max_pages: int = 2,
    domain: str = "com.tr",
) -> CategoryConfig:
    """Yeni kategori ekle. url_or_query http ile başlamıyorsa arama URL'si üretir."""
    name = name.strip()
    url_or_query = url_or_query.strip()
    if not name:
        raise ValueError("Kategori adı boş olamaz.")
    if not url_or_query:
        raise ValueError("URL veya arama ifadesi gerekli.")
    if max_pages < 0:
        raise ValueError("max_pages 0 veya pozitif olmalı.")

    if url_or_query.lower().startswith("http"):
        url = url_or_query
    else:
        url = build_amazon_search_url(url_or_query, domain=domain)

    with _lock:
        config = load_app_config(path)
        for existing in config.categories:
            if existing.name.lower() == name.lower():
                raise ValueError(f"Bu isimde kategori zaten var: {existing.name}")
            if existing.url == url:
                raise ValueError(f"Bu URL zaten ekli: {existing.name}")

        category = CategoryConfig(
            name=name,
            url=url,
            enabled=True,
            max_pages=max_pages,
        )
        config.categories.append(category)
        _write_config(Path(path), config)
        return category


def find_category(config: AppConfig, name: str) -> CategoryConfig | None:
    """Ada göre kategori bul (tam eşleşme, yoksa tekil kısmi eşleşme)."""
    needle = name.strip().lower()
    if not needle:
        return None
    exact = [c for c in config.categories if c.name.lower() == needle]
    if exact:
        return exact[0]
    partial = [c for c in config.categories if needle in c.name.lower()]
    if len(partial) == 1:
        return partial[0]
    return None


def set_category_enabled(
    path: str | Path, name: str, enabled: bool
) -> CategoryConfig:
    """Kategoriyi aktif/pasif yap."""
    with _lock:
        config = load_app_config(path)
        category = find_category(config, name)
        if category is None:
            # Ambiguous vs missing
            needle = name.strip().lower()
            matches = [c.name for c in config.categories if needle in c.name.lower()]
            if len(matches) > 1:
                raise ValueError(
                    "Birden fazla eşleşme: " + ", ".join(matches)
                )
            raise ValueError(f"Kategori bulunamadı: {name}")

        category.enabled = enabled
        _write_config(Path(path), config)
        return category
