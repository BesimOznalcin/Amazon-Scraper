# Amazon Fiyat Takipçisi

Oxylabs [amazon-scraper](https://github.com/oxylabs/amazon-scraper) örneğindeki gibi Selenium ile Amazon kategori sayfalarını tarar, fiyat düşüşlerini SQLite'ta takip eder ve **Telegram** veya **Email** ile bildirir.

## Kurulum

```powershell
cd "C:\Users\Besim.Oznalcin\Desktop\Amazon Scraper"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Chrome yüklü olmalı (senin makinede var).

## Ayarlar

### 1. Kategoriler — `config.yaml`

Amazon'da istediğin kategori/arama sayfasını aç, URL'yi kopyala:

```yaml
categories:
  - name: "Laptop"
    url: "https://www.amazon.com.tr/s?k=laptop&rh=n%3A12466496031"
    enabled: true
  - name: "Kulaklık"
    url: "https://www.amazon.com.tr/s?k=kulaklık"
    enabled: true
```

### 2. Bildirimler — `.env`

**Telegram (önerilen):**
1. Telegram'da [@BotFather](https://t.me/BotFather) → `/newbot` → token al
2. Botuna bir mesaj yaz
3. Chat ID: `https://api.telegram.org/bot<TOKEN>/getUpdates`
4. `.env` içine yaz:

```
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=987654321
```

**Email (opsiyonel):** Gmail için uygulama şifresi kullan.

```
EMAIL_ENABLED=true
SMTP_USER=sen@gmail.com
SMTP_PASSWORD=uygulama-sifresi
EMAIL_TO=sen@gmail.com
```

## Kullanım

```powershell
# Bildirim testi
python -m price_tracker test-notify

# Tek seferlik tarama
python -m price_tracker run

# Her 60 dakikada bir (Ctrl+C ile dur)
python -m price_tracker schedule

# Farklı aralık
python -m price_tracker schedule --interval 30
```

**Not:** İlk taramada fiyat geçmişi yok; düşüş bildirimi 2. taramadan itibaren gelir.

## Nasıl çalışır?

1. `config.yaml` içindeki her kategori URL'si Selenium + Chrome ile açılır  
2. Ürünler (ASIN, başlık, fiyat, link) parse edilir  
3. SQLite (`data/prices.db`) ile önceki fiyat karşılaştırılır  
4. `MIN_DROP_PERCENT` (varsayılan %5) üzeri düşüşler Telegram/Email ile gönderilir  

## Uyarı

Amazon bot koruması agresif olabilir (CAPTCHA). Yoğun taramada engellenebilirsin; aralığı düşük tut (ör. 60+ dk). Büyük ölçek için Oxylabs API tarafı daha dayanıklıdır.
