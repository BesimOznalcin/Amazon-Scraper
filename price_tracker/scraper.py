"""Amazon category page scraper (Selenium, oxylabs free-scraper tarzı)."""

from __future__ import annotations

import logging
import re
import time
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from webdriver_manager.chrome import ChromeDriverManager

from price_tracker.models import Product

logger = logging.getLogger(__name__)
logging.getLogger("WDM").setLevel(logging.ERROR)

DP_ASIN_RE = re.compile(r"/dp/([A-Z0-9]{10})")
# Amazon sonuçları pratikte sonsuz uzayabilir; güvenlik tavanı
HARD_PAGE_CAP = 100


class AmazonScraper:
    def __init__(self, domain: str = "com.tr", headless: bool = True) -> None:
        self.domain = domain.lstrip(".")
        self.headless = headless

    def _init_driver(self) -> webdriver.Chrome:
        options = Options()
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--lang=tr-TR")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": (
                    "Object.defineProperty(navigator, 'webdriver', "
                    "{get: () => undefined})"
                )
            },
        )
        return driver

    def _parse_price_text(self, text: str) -> float | None:
        if not text:
            return None
        cleaned = re.sub(r"[^\d.,]", "", text.strip())
        if not cleaned:
            return None

        if "," in cleaned and "." in cleaned:
            if cleaned.rfind(",") > cleaned.rfind("."):
                cleaned = cleaned.replace(".", "").replace(",", ".")
            else:
                cleaned = cleaned.replace(",", "")
        elif "," in cleaned:
            parts = cleaned.split(",")
            if len(parts[-1]) <= 2:
                cleaned = cleaned.replace(",", ".")
            else:
                cleaned = cleaned.replace(",", "")

        try:
            return float(cleaned)
        except ValueError:
            return None

    def _parse_price_for_product(self, product: WebElement) -> float | None:
        try:
            offscreen = product.find_element(
                By.CSS_SELECTOR, "span.a-price span.a-offscreen"
            )
            price = self._parse_price_text(offscreen.get_attribute("textContent") or "")
            if price is not None:
                return price
        except NoSuchElementException:
            pass

        try:
            whole_el = product.find_element(By.CSS_SELECTOR, "span.a-price-whole")
            whole = (whole_el.text or "").replace(".", "").replace(",", "").strip()
            try:
                frac_el = product.find_element(By.CSS_SELECTOR, "span.a-price-fraction")
                frac = (frac_el.text or "").strip()
            except NoSuchElementException:
                frac = "00"
            if whole:
                return self._parse_price_text(f"{whole}.{frac}")
        except NoSuchElementException:
            return None
        return None

    def _absolute_url(self, href: str | None) -> str | None:
        if not href:
            return None
        if href.startswith("http"):
            return href.split("?")[0]
        return f"https://www.amazon.{self.domain}{href.split('?')[0]}"

    def _extract_asin(self, element: WebElement) -> str | None:
        asin = element.get_attribute("data-asin")
        if asin and re.fullmatch(r"[A-Z0-9]{10}", asin):
            return asin

        item_id = element.get_attribute("data-csa-c-item-id") or ""
        match = re.search(r"amzn1\.asin\.([A-Z0-9]{10})", item_id)
        if match:
            return match.group(1)

        try:
            link = element.find_element(By.CSS_SELECTOR, "a[href*='/dp/']")
            href = link.get_attribute("href") or ""
            dp = DP_ASIN_RE.search(href)
            if dp:
                return dp.group(1)
        except NoSuchElementException:
            pass
        return None

    def _parse_product(self, element: WebElement) -> Product | None:
        asin = self._extract_asin(element)
        if not asin:
            return None

        title = ""
        for selector in (
            "h2 span",
            "h2 a span",
            "a.a-link-normal.a-text-normal",
            ".a-text-normal",
            "img[alt]",
        ):
            try:
                el = element.find_element(By.CSS_SELECTOR, selector)
                title = (el.text or "").strip()
                if not title and selector == "img[alt]":
                    title = (el.get_attribute("alt") or "").strip()
                if title:
                    break
            except NoSuchElementException:
                continue

        if not title:
            return None

        try:
            url_el = element.find_element(By.CSS_SELECTOR, "a[href*='/dp/']")
            url = self._absolute_url(url_el.get_attribute("href"))
        except NoSuchElementException:
            url = f"https://www.amazon.{self.domain}/dp/{asin}"

        image_url = None
        try:
            img_el = element.find_element(By.CSS_SELECTOR, "img[src]")
            image_url = img_el.get_attribute("src")
        except NoSuchElementException:
            pass

        price = self._parse_price_for_product(element)
        if price is None:
            logger.debug("Fiyat yok (stok dışı olabilir): %s", title[:60])

        return Product(
            title=title,
            url=url or f"https://www.amazon.{self.domain}/dp/{asin}",
            asin_code=asin,
            image_url=image_url,
            price=price,
            currency="TRY" if self.domain == "com.tr" else None,
        )

    def _find_product_elements(self, driver: webdriver.Chrome) -> list[WebElement]:
        classic = driver.find_elements(
            By.CSS_SELECTOR, "div[data-component-type='s-search-result']"
        )
        if classic:
            return classic

        puis = driver.find_elements(
            By.CSS_SELECTOR, "[data-csa-c-item-id*='amzn1.asin.']"
        )
        if puis:
            return puis

        return driver.find_elements(By.CSS_SELECTOR, "div.puis-card-container")

    def _has_next_page(self, driver: webdriver.Chrome) -> bool:
        for selector in (
            "a.s-pagination-next:not(.s-pagination-disabled)",
            "li.a-last:not(.a-disabled) a",
            ".a-pagination .a-last:not(.a-disabled) a",
        ):
            try:
                el = driver.find_element(By.CSS_SELECTOR, selector)
                href = el.get_attribute("href") or ""
                aria = (el.get_attribute("aria-disabled") or "").lower()
                if aria == "true":
                    continue
                if href or el.is_displayed():
                    return True
            except NoSuchElementException:
                continue
        return False

    def _accept_cookies(self, driver: webdriver.Chrome) -> None:
        for selector in (
            "#sp-cc-accept",
            "input#sp-cc-accept",
            "[data-action='accept']",
        ):
            try:
                btn = driver.find_element(By.CSS_SELECTOR, selector)
                btn.click()
                time.sleep(1)
                return
            except NoSuchElementException:
                continue

    def normalize_category_url(self, url: str) -> str:
        """/b/?node=... browse URL'lerini /s?rh=n:... arama formatına çevir."""
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        node = (qs.get("node") or [None])[0]
        if node and ("/b/" in parsed.path or "node=" in url):
            return f"https://www.amazon.{self.domain}/s?rh=n%3A{node}"
        return url

    def _url_with_page(self, url: str, page: int) -> str:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        qs["page"] = [str(page)]
        query = urlencode({k: v if len(v) > 1 else v[0] for k, v in qs.items()}, doseq=True)
        return urlunparse(parsed._replace(query=query))

    def _parse_products_on_page(
        self, driver: webdriver.Chrome, seen: set[str]
    ) -> list[Product]:
        products: list[Product] = []
        for el in self._find_product_elements(driver):
            try:
                product = self._parse_product(el)
                if product and product.asin_code not in seen:
                    seen.add(product.asin_code)
                    products.append(product)
            except Exception:
                logger.exception("Ürün parse edilemedi, atlanıyor.")
        return products

    def scrape_page(self, url: str, max_pages: int = 1) -> list[Product]:
        """Tek veya çok sayfalı tarama. max_pages<=0 → mümkün olan tüm sayfalar."""
        url = self.normalize_category_url(url)
        parsed = urlparse(url)
        if "amazon." not in (parsed.netloc or ""):
            raise ValueError(f"Geçersiz Amazon URL: {url}")

        if max_pages <= 0:
            page_limit = HARD_PAGE_CAP
        else:
            page_limit = min(max_pages, HARD_PAGE_CAP)

        logger.info("Kategori taranıyor (max %d sayfa): %s", page_limit, url)
        driver = None
        products: list[Product] = []
        seen: set[str] = set()

        try:
            driver = self._init_driver()
            for page in range(1, page_limit + 1):
                page_url = self._url_with_page(url, page) if page > 1 else url
                logger.info("Sayfa %d/%d: %s", page, page_limit, page_url)
                driver.get(page_url)
                time.sleep(3 if page > 1 else 4)

                if page == 1:
                    self._accept_cookies(driver)

                page_products = self._parse_products_on_page(driver, seen)
                products.extend(page_products)
                logger.info(
                    "Sayfa %d: %d ürün okundu (oturum toplamı %d)",
                    page,
                    len(page_products),
                    len(products),
                )

                if not page_products:
                    logger.info("Boş sayfa — sayfalama durdu.")
                    break

                if page < page_limit and not self._has_next_page(driver):
                    logger.info("Sonraki sayfa yok — sayfalama bitti.")
                    break

                # Nazik bekleme (rate limit / CAPTCHA riskini azaltır)
                time.sleep(1.5)

            logger.info("Toplam %d ürün bulundu (%d benzersiz ASIN).", len(products), len(seen))
            return products
        except WebDriverException:
            logger.exception("Selenium hatası: %s", url)
            raise
        finally:
            if driver is not None:
                driver.quit()
