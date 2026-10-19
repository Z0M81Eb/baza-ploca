from curl_cffi import requests as cffi_requests
import cloudscraper
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context
from bs4 import BeautifulSoup
import csv
import os
import re
import time
import html
from urllib.parse import urljoin

SITE_URL = "https://analogni-zvuk.hr"
CSV_FILENAME = "analognizvuk_ploce.csv"

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "hr-HR,hr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://analogni-zvuk.hr/"
}

class TlsAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        context = create_urllib3_context()
        context.set_ciphers('DEFAULT@SECLEVEL=1')
        kwargs['ssl_context'] = context
        return super(TlsAdapter, self).init_poolmanager(*args, **kwargs)

def normaliziraj_link_proizvoda(href):
    if not href:
        return ""
    puni = urljoin(SITE_URL, href.strip())
    return puni.split('?')[0].rstrip('/') + '/'

def ocisti_url_slike(raw_url, is_srcset=False):
    if not raw_url:
        return ""
    val = html.unescape(str(raw_url)).strip()
    val_lower = val.lower()

    if val_lower.startswith('data:image') or any(x in val_lower for x in ['placeholder', 'blank.', 'spinner', 'loader', '.svg', '1x1', 'emoji', 'gravatar']):
        return ""

    if is_srcset:
        kandidati = re.split(r'\s+\d+[wx]\s*,?\s*', val)
        kandidati = [k.strip().rstrip(',') for k in kandidati if k.strip()]
        if not kandidati:
            return ""
        val = kandidati[0]
        if ' ' in val and re.search(r'\s+\d+[wx]$', val):
            val = re.sub(r'\s+\d+[wx]$', '', val).strip()

    if val.startswith('//'):
        val = 'https:' + val
    elif val.startswith('/'):
        val = urljoin(SITE_URL, val)

    if not val.startswith('http'):
        return ""

    return val.replace(' ', '%20')

def izvuci_sliku_iz_elementa(el):
    if not el:
        return ""

    imgs = [el] if getattr(el, 'name', None) == 'img' else el.find_all('img')
    lazy_attrs = [
        'data-lazy-src', 'data-src', 'data-original', 'data-large_image',
        'data-opt-src', 'nitro-lazy-src', 'data-breeze', 'data-wood-src',
        'data-src-img', 'data-echo', 'src'
    ]
    srcset_attrs = ['data-lazy-srcset', 'data-srcset', 'srcset']

    for img in imgs:
        for attr in lazy_attrs:
            url = ocisti_url_slike(img.get(attr, ''), is_srcset=False)
            if url:
                return url
        for attr in srcset_attrs:
            url = ocisti_url_slike(img.get(attr, ''), is_srcset=True)
            if url:
                return url
        for attr_name, attr_val in img.attrs.items():
            if isinstance(attr_val, str) and '/wp-content/uploads/' in attr_val:
                url = ocisti_url_slike(attr_val, is_srcset=('srcset' in attr_name.lower()))
                if url:
                    return url

    for source in el.find_all('source'):
        for attr in ['data-srcset', 'srcset']:
            url = ocisti_url_slike(source.get(attr, ''), is_srcset=True)
            if url:
                return url
        url = ocisti_url_slike(source.get('src', ''), is_srcset=False)
        if url:
            return url

    raw_html = str(el)
    for m in re.finditer(r'<img[^>]+(?:data-lazy-src|data-src|src)=["\']([^"\']+)["\']', raw_html, re.IGNORECASE):
        url = ocisti_url_slike(m.group(1), is_srcset=False)
        if url:
            return url

    for m in re.finditer(r'background-image\s*:\s*url\(["\']?([^"\')]+)["\']?\)', raw_html, re.IGNORECASE):
        url = ocisti_url_slike(m.group(1), is_srcset=False)
        if url:
            return url

    upload_match = re.search(r'(https?://[^"\'\s<>)]+/wp-content/uploads/[^"\'\s<>)]+\.(?:jpg|jpeg|png|webp))', raw_html, re.IGNORECASE)
    if upload_match:
        url = ocisti_url_slike(upload_match.group(1), is_srcset=False)
        if url:
            return url

    return ""

def dohvati_url(url, cffi_sess, cloud_sess, timeout=30):
    """Pokušava prvo kroz cloudscraper+TlsAdapter (zbog SECLEVEL=1), a zatim kroz curl_cffi."""
    for s in (cloud_sess, cffi_sess):
        try:
            r = s.get(url, timeout=timeout)
            if r.status_code in [200, 400, 404]:
                return r
        except Exception:
            continue
    return None

def dohvati_sliku_sa_stranice_proizvoda(product_url, cffi_sess, cloud_sess):
    r = dohvati_url(product_url, cffi_sess, cloud_sess, timeout=15)
    if not r or r.status_code != 200:
        return ""
    try:
        s = BeautifulSoup(r.text, 'lxml')
        og = s.find('meta', property='og:image') or s.find('meta', attrs={'name': 'twitter:image'})
        if og and og.get('content'):
            url = ocisti_url_slike(og['content'], is_srcset=False)
            if url:
                return url
        galerija = s.select_one('.woocommerce-product-gallery, .product-images, .images')
        if galerija:
            return izvuci_sliku_iz_elementa(galerija)
    except Exception:
        pass
    return ""

def izvuci_cijenu(tekst):
    svi_brojevi = re.findall(r'(\d{1,4}[.,]\d{2})\s*€', tekst)
    if not svi_brojevi:
        svi_brojevi = re.findall(r'(\d{1,4}[.,]\d{2})', tekst)
    if svi_brojevi:
        dijelovi = svi_brojevi[-1].replace(',', '.').split('.')
        if len(dijelovi) == 2:
            try:
                val = float(f"{dijelovi[0]}.{dijelovi[1]}")
                if val > 0:
                    return f"{val:.2f}"
            except Exception:
                pass
    return "0.00"

print("ANALOGNI ZVUK: Pokrećem hibridni API + HTML sistem sa 100% zaštitom slika...", flush=True)

sve_ploce = {}
vidjeni_linkovi = set()
uspjesno_skenirano = False

if os.path.exists(CSV_FILENAME):
    with open(CSV_FILENAME, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if len(row) >= 7:
                norm_k = normaliziraj_link_proizvoda(row[2])
                row[2] = norm_k
                sve_ploce[norm_k] = row
    print(f"📦 Učitano {len(sve_ploce)} postojećih ploča.", flush=True)

cloud_sess = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})
cloud_sess.mount('https://', TlsAdapter())
cloud_sess.headers.update(HEADERS)

cffi_sess = cffi_requests.Session(impersonate="chrome120")
cffi_sess.headers.update(HEADERS)

print("\nProbijanje početne zaštite (Naslovnica)...", flush=True)
dohvati_url("https://analogni-zvuk.hr/", cffi_sess, cloud_sess, timeout=25)
time.sleep(1)

# === 1. POKUŠAJ PREKO WOOCOMMERCE STORE API-ja ===
print("\n--- Pokušavam brzi WooCommerce Store API ---", flush=True)
page = 1
api_radi = False

while True:
    api_url = f"https://analogni-zvuk.hr/wp-json/wc/store/products?page={page}&per_page=100"
    res = dohvati_url(api_url, cffi_sess, cloud_sess, timeout=30)

    if res is None:
        print("API nije odgovorio, prelazim na HTML...", flush=True)
        break
    if res.status_code == 400:
        uspjesno_skenirano = True
        break
    if res.status_code != 200 or not res.text.strip().startswith('['):
        print(f"API nije dostupan (Status: {res.status_code}), prelazim na HTML...", flush=True)
        break

    try:
        data = res.json()
    except Exception:
        break

    if not data:
        uspjesno_skenirano = True
        break

    api_radi = True
    dodano = 0
    sa_slikom = 0

    for item in data:
        if not item.get('is_in_stock', False):
            continue

        # Provjeri kategorije da ne uvezemo CD, kasete ili opremu ako postoje
        kategorije_slugs = " ".join([k.get('slug', '').lower() for k in item.get('categories', [])])
        if any(zabranjeno in kategorije_slugs for zabranjeno in ['cd', 'kaset', 'kazet', 'oprema', 'gramofon', 'cetk', 'igla']):
            if not any(dopusteno in kategorije_slugs for dopusteno in ['ploc', 'vinil', 'vinyl', 'lp', 'singl']):
                continue

        title = html.unescape(item.get('name', '')).strip()
        link = normaliziraj_link_proizvoda(item.get('permalink', ''))
        if not title or not link:
            continue

        prices = item.get('prices', {})
        raw_price = prices.get('price', '0')
        minor_unit = prices.get('currency_minor_unit', 2)
        try:
            price_val = float(raw_price) / (10 ** minor_unit)
            price = f"{price_val:.2f}"
        except Exception:
            price = "0.00"

        if price == "0.00":
            continue

        images = item.get('images', [])
        img_url = ocisti_url_slike(images[0].get('src', ''), is_srcset=False) if images else ""
        if not img_url and link in sve_ploce and len(sve_ploce[link]) >= 4:
            img_url = ocisti_url_slike(sve_ploce[link][3], is_srcset=False)

        if img_url:
            sa_slikom += 1

        stanje_medija = "Second Hand"
        stanje_omota = "Second Hand"
        for attr in item.get('attributes', []):
            aname = attr.get('name', '').lower()
            terms = attr.get('terms', [])
            tval = terms[0].get('name', '') if terms else ''
            if 'stanje' in aname or 'uvjet' in aname:
                if 'nov' in tval.lower():
                    stanje_medija = "Novo"
                    stanje_omota = "Novo"
                elif tval:
                    stanje_medija = tval
                    stanje_omota = tval

        sve_ploce[link] = [title, price, link, img_url, stanje_medija, stanje_omota, "Vinil"]
        vidjeni_linkovi.add(link)
        dodano += 1

    print(f"API Paket {page} -> OK ({dodano} ploča, sa slikom: {sa_slikom}/{dodano})", flush=True)
    page += 1
    time.sleep(0.5)

# === 2. FALLBACK NA HTML AKO API NIJE DOSTUPAN ===
if not api_radi:
    print("\n--- Pokrećem HTML skeniranje kataloga ---", flush=True)
    current_page_num = 1
    while True:
        cat_url = "https://analogni-zvuk.hr/product-category/gramofonske-ploce/"
        if current_page_num > 1:
            cat_url += f"page/{current_page_num}/"

        print(f"Stranica {current_page_num}...", end=" ", flush=True)
        response = dohvati_url(cat_url, cffi_sess, cloud_sess, timeout=30)
        if not response:
            print("-> Greška spajanja.", flush=True)
            break
        if response.status_code == 404:
            print("-> Kraj (404).", flush=True)
            uspjesno_skenirano = True
            break
        if response.status_code != 200:
            print(f"-> Neočekivan status: {response.status_code}", flush=True)
            break

        soup = BeautifulSoup(response.text, 'lxml')
        products = soup.select('li.product, div.product, .type-product')

        if not products:
            print("-> Nema više proizvoda.", flush=True)
            if current_page_num > 1:
                uspjesno_skenirano = True
            break

        dodano = 0
        sa_slikom = 0
        for prod in products:
            klase = " ".join(prod.get('class', [])).lower()
            if 'outofstock' in klase:
                continue

            a_tag = prod.find('a', class_='woocommerce-LoopProduct-link') or prod.find('a', href=True)
            if not a_tag or not a_tag.get('href'):
                continue
            link = normaliziraj_link_proizvoda(a_tag['href'])

            title_el = prod.select_one('.woocommerce-loop-product__title, .product-title, h2, h3')
            if not title_el:
                continue
            title = html.unescape(title_el.get_text(" ", strip=True)).strip()

            bdi = prod.select_one('.price ins bdi, .price bdi, .price')
            price = izvuci_cijenu(bdi.get_text(" ", strip=True) if bdi else prod.get_text(" ", strip=True))
            if not title or price == "0.00":
                continue

            img_url = izvuci_sliku_iz_elementa(prod)
            if not img_url and link in sve_ploce and len(sve_ploce[link]) >= 4:
                img_url = ocisti_url_slike(sve_ploce[link][3], is_srcset=False)
            if not img_url:
                img_url = dohvati_sliku_sa_stranice_proizvoda(link, cffi_sess, cloud_sess)

            if img_url:
                sa_slikom += 1

            sve_ploce[link] = [title, price, link, img_url, "Second Hand", "Second Hand", "Vinil"]
            vidjeni_linkovi.add(link)
            dodano += 1

        print(f"-> OK ({dodano} ploča, sa slikom: {sa_slikom}/{dodano})", flush=True)
        current_page_num += 1
        time.sleep(0.8)

# === 3. SIGURNO BRISANJE I SPREMANJE ===
if uspjesno_skenirano and len(vidjeni_linkovi) > 20:
    pocetni = len(sve_ploce)
    sve_ploce = {k: v for k, v in sve_ploce.items() if k in vidjeni_linkovi}
    print(f"\n🧹 Sinkronizacija prodanih ploča uspješna (Obrisano: {pocetni - len(sve_ploce)}).", flush=True)
else:
    print(f"\n⚠️ UPOZORENJE: Pronađeno {len(vidjeni_linkovi)} linkova. Zadržavam postojeću bazu.", flush=True)

if sve_ploce:
    with open(CSV_FILENAME, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Naslov', 'Cijena', 'URL_Proizvoda', 'URL_Slike', 'Stanje_Medija', 'Stanje_Omota', 'Tip_Artikla'])
        writer.writerows(sve_ploce.values())
    ukupno_slika = sum(1 for v in sve_ploce.values() if len(v) >= 4 and v[3])
    print(f"🎉 Gotovo! Ukupno u CSV-u: {len(sve_ploce)} (Sa slikom: {ukupno_slika})", flush=True)
