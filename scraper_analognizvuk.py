import cloudscraper
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context
from bs4 import BeautifulSoup
import csv
import os
import time
import html

class TlsAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        context = create_urllib3_context()
        context.set_ciphers('DEFAULT@SECLEVEL=1')
        kwargs['ssl_context'] = context
        return super(TlsAdapter, self).init_poolmanager(*args, **kwargs)

print("ANALOGNI ZVUK: Pokrećem hibridni API + Cloudscraper sistem...", flush=True)

csv_filename = 'analognizvuk_ploce.csv'
sve_ploce = {}
vidjeni_linkovi = set()
uspjesno_skenirano = False

if os.path.exists(csv_filename):
    with open(csv_filename, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if len(row) >= 7:
                sve_ploce[row[2]] = row
    print(f"Učitano {len(sve_ploce)} postojećih ploča.", flush=True)

scraper = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
)
scraper.mount('https://', TlsAdapter())

print("\nProbijanje početne zaštite (Naslovnica)...", flush=True)
try:
    scraper.get("https://analogni-zvuk.hr/", timeout=30)
    time.sleep(3)
except Exception as e:
    print(f"Upozorenje na naslovnici: {e}", flush=True)

# === 1. POKUŠAJ PREKO WOOCOMMERCE STORE API-ja ===
print("\n--- Pokušavam brzi WooCommerce Store API ---", flush=True)
page = 1
api_radi = False

while True:
    api_url = f"https://analogni-zvuk.hr/wp-json/wc/store/products?page={page}&per_page=100"
    try:
        res = scraper.get(api_url, timeout=30)
        if res.status_code == 400:
            uspjesno_skenirano = True
            break
        if res.status_code != 200 or not res.text.strip().startswith('['):
            print(f"API nije dostupan (Status: {res.status_code}), prelazim na HTML...", flush=True)
            break

        data = res.json()
        if not data:
            uspjesno_skenirano = True
            break

        api_radi = True
        dodano = 0
        for item in data:
            if not item.get('is_in_stock', False):
                continue

            title = html.unescape(item.get('name', '')).strip()
            link = item.get('permalink', '')
            if not title or not link:
                continue

            prices = item.get('prices', {})
            raw_price = prices.get('price', '0')
            minor_unit = prices.get('currency_minor_unit', 2)
            try:
                price_val = float(raw_price) / (10 ** minor_unit)
                price = f"{price_val:.2f}"
            except:
                price = "0.00"

            if price == "0.00":
                continue

            images = item.get('images', [])
            img_url = images[0].get('src', '') if images else ""

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
                    else:
                        stanje_medija = tval
                        stanje_omota = tval

            sve_ploce[link] = [title, price, link, img_url, stanje_medija, stanje_omota, "Vinil"]
            vidjeni_linkovi.add(link)
            dodano += 1

        print(f"API Paket {page} -> OK ({dodano} ploča)", flush=True)
        page += 1
        time.sleep(0.5)
    except Exception as e:
        print(f"API Greška na stranici {page}: {e}", flush=True)
        break

# === 2. FALLBACK NA HTML AKO API NIJE DOSTUPAN ===
if not api_radi:
    print("\n--- Pokrećem HTML skeniranje kataloga ---", flush=True)
    current_page_num = 1
    while True:
        cat_url = "https://analogni-zvuk.hr/product-category/gramofonske-ploce/"
        if current_page_num > 1:
            cat_url += f"page/{current_page_num}/"

        print(f"Stranica {current_page_num}...", end=" ", flush=True)
        try:
            response = scraper.get(cat_url, timeout=30)
            if response.status_code == 404:
                print("-> Kraj.", flush=True)
                uspjesno_skenirano = True
                break

            soup = BeautifulSoup(response.text, 'html.parser')
            products = soup.select('li.product')

            if not products:
                print("-> Nema proizvoda.", flush=True)
                break

            dodano = 0
            for prod in products:
                if 'outofstock' in prod.get('class', []):
                    continue
                a_tag = prod.find('a', class_='woocommerce-LoopProduct-link') or prod.find('a')
                if not a_tag or not a_tag.get('href'):
                    continue
                link = a_tag['href']
                title_el = prod.find(class_='woocommerce-loop-product__title') or prod.find('h2')
                if not title_el:
                    continue
                title = title_el.text.strip()

                price = ""
                bdi = prod.select_one('.price ins bdi') or prod.select_one('.price bdi')
                if bdi:
                    price = bdi.text.replace('€', '').replace(',', '.').replace('\xa0', '').strip()

                img_el = prod.find('img')
                img_url = (img_el.get('data-src') or img_el.get('src') or "") if img_el else ""

                if title and price:
                    sve_ploce[link] = [title, price, link, img_url, "Second Hand", "Second Hand", "Vinil"]
                    vidjeni_linkovi.add(link)
                    dodano += 1

            print(f"-> OK ({dodano})", flush=True)
            current_page_num += 1
            time.sleep(1)
        except Exception as e:
            print(f"Greška: {e}", flush=True)
            break

# === 3. SIGURNO BRISANJE I SPREMANJE ===
if uspjesno_skenirano and len(vidjeni_linkovi) > 20:
    sve_ploce = {k: v for k, v in sve_ploce.items() if k in vidjeni_linkovi}
    print(f"\nSinkronizacija prodanih ploča uspješna.", flush=True)
else:
    print(f"\nUPOZORENJE: Pronađeno premalo linkova ({len(vidjeni_linkovi)}). Zadržavam postojeću bazu.", flush=True)

if sve_ploce:
    with open(csv_filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Naslov', 'Cijena', 'URL_Proizvoda', 'URL_Slike', 'Stanje_Medija', 'Stanje_Omota', 'Tip_Artikla'])
        writer.writerows(sve_ploce.values())

print(f"\nGotovo! Ukupno u CSV-u: {len(sve_ploce)}", flush=True)
