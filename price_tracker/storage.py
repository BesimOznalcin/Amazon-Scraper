"""SQLite storage for product price history."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from price_tracker.models import PriceChange, Product


class PriceChangeKind(str, Enum):
    NO_PRICE = "no_price"
    NEW = "new"
    UNCHANGED = "unchanged"
    INCREASED = "increased"
    DECREASED = "decreased"


class UpsertResult(BaseModel):
    kind: PriceChangeKind
    change: PriceChange | None = None


class PriceHistoryPoint(BaseModel):
    price: float
    old_price: float | None = None
    direction: str | None = None
    change_percent: float | None = None
    category: str | None = None
    recorded_at: str


class ProductRecord(BaseModel):
    asin: str
    title: str
    url: str
    image_url: str | None = None
    last_price: float | None = None
    lowest_price: float | None = None
    category: str | None = None
    updated_at: str
    history: list[PriceHistoryPoint] = []


class PriceStore:
    def __init__(self, db_path: str = "data/prices.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=60000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS products (
                    asin TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    image_url TEXT,
                    last_price REAL,
                    lowest_price REAL,
                    category TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asin TEXT NOT NULL,
                    price REAL NOT NULL,
                    old_price REAL,
                    direction TEXT,
                    change_percent REAL,
                    category TEXT,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY (asin) REFERENCES products(asin)
                );

                CREATE INDEX IF NOT EXISTS idx_history_asin
                    ON price_history(asin, recorded_at DESC);
                """
            )
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        product_cols = {row[1] for row in conn.execute("PRAGMA table_info(products)")}
        if "category" not in product_cols:
            conn.execute("ALTER TABLE products ADD COLUMN category TEXT")

        history_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(price_history)")
        }
        for col, typedef in (
            ("old_price", "REAL"),
            ("direction", "TEXT"),
            ("change_percent", "REAL"),
            ("category", "TEXT"),
        ):
            if col not in history_cols:
                conn.execute(f"ALTER TABLE price_history ADD COLUMN {col} {typedef}")

    def get_last_price(self, asin: str) -> float | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_price FROM products WHERE asin = ?",
                (asin,),
            ).fetchone()
            if row is None or row["last_price"] is None:
                return None
            return float(row["last_price"])

    def get_product_with_history(
        self, asin: str, limit: int = 30
    ) -> ProductRecord | None:
        asin = asin.strip().upper()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT asin, title, url, image_url, last_price, lowest_price,
                       category, updated_at
                FROM products WHERE asin = ?
                """,
                (asin,),
            ).fetchone()
            if row is None:
                return None

            history_rows = conn.execute(
                """
                SELECT price, old_price, direction, change_percent, category, recorded_at
                FROM price_history
                WHERE asin = ?
                ORDER BY recorded_at DESC, id DESC
                LIMIT ?
                """,
                (asin, limit),
            ).fetchall()

        history = [
            PriceHistoryPoint(
                price=float(h["price"]),
                old_price=float(h["old_price"]) if h["old_price"] is not None else None,
                direction=h["direction"],
                change_percent=(
                    float(h["change_percent"])
                    if h["change_percent"] is not None
                    else None
                ),
                category=h["category"],
                recorded_at=h["recorded_at"],
            )
            for h in history_rows
        ]
        return ProductRecord(
            asin=row["asin"],
            title=row["title"],
            url=row["url"],
            image_url=row["image_url"],
            last_price=(
                float(row["last_price"]) if row["last_price"] is not None else None
            ),
            lowest_price=(
                float(row["lowest_price"]) if row["lowest_price"] is not None else None
            ),
            category=row["category"],
            updated_at=row["updated_at"],
            history=history,
        )

    def upsert_product(self, product: Product) -> UpsertResult:
        """Ürünü kaydet / güncelle. Karşılaştırma sonucunu döner."""
        with self._lock:
            return self._upsert_product_unlocked(product)

    def _upsert_product_unlocked(self, product: Product) -> UpsertResult:
        """Ürünü kaydet / güncelle. Karşılaştırma sonucunu döner."""
        if product.price is None:
            return UpsertResult(kind=PriceChangeKind.NO_PRICE)

        now = datetime.now(timezone.utc).isoformat()
        old_price = self.get_last_price(product.asin_code)
        change: PriceChange | None = None
        kind: PriceChangeKind
        direction: str | None = None
        change_percent: float | None = None

        if old_price is None:
            kind = PriceChangeKind.NEW
            direction = "new"
        elif abs(old_price - product.price) <= 0.001:
            kind = PriceChangeKind.UNCHANGED
        elif product.price < old_price:
            kind = PriceChangeKind.DECREASED
            amount = old_price - product.price
            change_percent = (amount / old_price) * 100
            direction = "down"
            change = PriceChange(
                product=product,
                old_price=old_price,
                new_price=product.price,
                change_amount=amount,
                change_percent=change_percent,
                direction="down",
                category=product.category or "",
            )
        else:
            kind = PriceChangeKind.INCREASED
            amount = product.price - old_price
            change_percent = (amount / old_price) * 100
            direction = "up"
            change = PriceChange(
                product=product,
                old_price=old_price,
                new_price=product.price,
                change_amount=amount,
                change_percent=change_percent,
                direction="up",
                category=product.category or "",
            )

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT lowest_price FROM products WHERE asin = ?",
                (product.asin_code,),
            ).fetchone()

            if existing is None:
                lowest = product.price
            else:
                prev_low = existing["lowest_price"]
                lowest = (
                    min(prev_low, product.price)
                    if prev_low is not None
                    else product.price
                )

            conn.execute(
                """
                INSERT INTO products (
                    asin, title, url, image_url, last_price, lowest_price,
                    category, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asin) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
                    image_url = excluded.image_url,
                    last_price = excluded.last_price,
                    lowest_price = excluded.lowest_price,
                    category = COALESCE(excluded.category, products.category),
                    updated_at = excluded.updated_at
                """,
                (
                    product.asin_code,
                    product.title,
                    product.url,
                    product.image_url,
                    product.price,
                    lowest,
                    product.category,
                    now,
                ),
            )

            if kind != PriceChangeKind.UNCHANGED:
                conn.execute(
                    """
                    INSERT INTO price_history (
                        asin, price, old_price, direction, change_percent,
                        category, recorded_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        product.asin_code,
                        product.price,
                        old_price,
                        direction,
                        change_percent,
                        product.category,
                        now,
                    ),
                )

        return UpsertResult(kind=kind, change=change)

    def count_products_by_category(self) -> list[tuple[str, int, int]]:
        """[(kategori, toplam_ürün, fiyatı_olan)] sıralı liste."""
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT
                        COALESCE(NULLIF(TRIM(category), ''), '(kategori yok)') AS cat,
                        COUNT(*) AS total,
                        SUM(CASE WHEN last_price IS NOT NULL THEN 1 ELSE 0 END) AS with_price
                    FROM products
                    GROUP BY cat
                    ORDER BY total DESC, cat ASC
                    """
                ).fetchall()
        return [
            (str(r["cat"]), int(r["total"]), int(r["with_price"] or 0)) for r in rows
        ]

    def count_products_for_category(self, category: str) -> tuple[int, int]:
        """Belirli kategori için (toplam, fiyatı_olan)."""
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN last_price IS NOT NULL THEN 1 ELSE 0 END) AS with_price
                    FROM products
                    WHERE category = ?
                    """,
                    (category,),
                ).fetchone()
        if row is None:
            return 0, 0
        return int(row["total"]), int(row["with_price"] or 0)

    def total_product_counts(self) -> tuple[int, int]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN last_price IS NOT NULL THEN 1 ELSE 0 END) AS with_price
                    FROM products
                    """
                ).fetchone()
        return int(row["total"]), int(row["with_price"] or 0)
