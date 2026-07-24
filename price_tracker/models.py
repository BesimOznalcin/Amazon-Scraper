"""Pydantic models for Amazon price tracker."""

from pydantic import BaseModel, Field


class Product(BaseModel):
    title: str
    url: str
    asin_code: str
    image_url: str | None = None
    price: float | None = None
    currency: str | None = None
    category: str | None = None


class PriceChange(BaseModel):
    product: Product
    old_price: float
    new_price: float
    change_amount: float
    change_percent: float
    direction: str  # "down" | "up"
    category: str = ""

    @property
    def is_drop(self) -> bool:
        return self.direction == "down"

    @property
    def summary(self) -> str:
        sign = "-" if self.is_drop else "+"
        return (
            f"{self.product.title[:80]}\n"
            f"Eski: {self.old_price:.2f} → Yeni: {self.new_price:.2f} "
            f"({sign}{self.change_amount:.2f} / %{self.change_percent:.1f})\n"
            f"{self.product.url}"
        )


# Geriye uyumluluk
PriceDrop = PriceChange


class CategoryConfig(BaseModel):
    name: str
    url: str
    enabled: bool = True
    min_drop_percent: float | None = None
    min_increase_percent: float | None = None
    # Bu kategori için max sayfa. Yoksa global MAX_PAGES kullanılır. 0 = hepsi.
    max_pages: int | None = None


class AppConfig(BaseModel):
    # Her tarama sonrası Telegram özet raporu
    status_notifications: bool = False
    categories: list[CategoryConfig] = Field(default_factory=list)


class CategoryScanResult(BaseModel):
    name: str
    product_count: int = 0
    drop_count: int = 0
    increase_alert_count: int = 0
    new_count: int = 0
    unchanged_count: int = 0
    increased_count: int = 0
    decreased_count: int = 0
    no_price_count: int = 0
    pages_hint: str = ""
    error: str | None = None


class ScanSummary(BaseModel):
    categories: list[CategoryScanResult] = Field(default_factory=list)
    drop_count: int = 0
    increase_alert_count: int = 0
    new_count: int = 0
    unchanged_count: int = 0
    increased_count: int = 0
    decreased_count: int = 0
    no_price_count: int = 0
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: int = 0

    @property
    def duration_label(self) -> str:
        secs = max(0, self.duration_seconds)
        minutes, seconds = divmod(secs, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours} sa {minutes} dk {seconds} sn"
        if minutes:
            return f"{minutes} dk {seconds} sn"
        return f"{seconds} sn"

    @property
    def total_products(self) -> int:
        return sum(c.product_count for c in self.categories)

    @property
    def ok_categories(self) -> int:
        return sum(1 for c in self.categories if c.error is None)

    @property
    def failed_categories(self) -> int:
        return sum(1 for c in self.categories if c.error is not None)
