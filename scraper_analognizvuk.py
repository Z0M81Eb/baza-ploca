from curl_cffi import requests as cffi_requests
import cloudscraper
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

def normaliziraj_link_proizvoda(href):
    if not href:
        return ""
    puni = urljoin(SITE_URL, href.strip())
    return puni.split('?')[0].rstrip('/') + '/'

def ocisti_url_slike(raw_url, is_srcset=False):
    if not raw_url: return ""
    val = html.unescape(str(raw_url)).strip()
    if val.startswith('//'): val = 'https:' + val
    elif val.startswith('/'): val = urljoin(SITE_URL, val)
    return val.replace(' ', '%20') if val.startswith('http') else ""

def dohvati_url(url, cffi_sess, timeout=30):
    try:
        r = cffi_sess.get(url, timeout=timeout)
        if r.status_code in [200, 400, 404]:
            return r
    except Exception:
        pass
    return None

def ocisti_stanje(sirovo):
    if not sirovo: return "VG+"
    s = str(sirovo).upper()
    if "NOVO" in s or "SEALED" in s or "MINT" in s and "NEAR" not in s: return "M"
    if "NEAR MINT" in s or "NM" in s: return "NM"
    if "EXCELLENT" in s or "EX" in s: return "EX"
    if "VERY GOOD PLUS" in s or "VG+" in s: return "VG+"
    if "VERY GOOD" in s or "VG" in s: return "VG"
    if "GOOD PLUS" in s or "G+" in s: return "G+"
    if "GOOD" in s or "G" in s: return "G"
    if "POOR" in s or "P" in s: return "P"
    return "VG+"

def dohvati_detalje_sa_stranice_proizvoda(product_url, cffi_sess):
    img_url = ""
    medij = "VG+"
    omot = "VG+"
    r = dohvati_url(product_url, cffi_sess, timeout=15)
    
    if not r or r.status_code != 200:
        return img_url, medij, omot
        
    try:
        soup = BeautifulSoup(r.text, 'lxml')
        
        # SLIKA
        og = soup.find('meta', property='og:image') or soup.find('meta', attrs={'name': 'twitter:image'})
        if og and og.get('content'):
            img_url = ocisti_url_slike(og['content'], is_srcset=False)
            
        # STANJE IZ TABA ILI TEKSTA
        tekst = soup.get_text("\n", strip=True)
        m_ploca = re.search(r'Stanje ploče:\s*(.*?)\n', tekst, re.I)
        m_omot = re.search(r'Stanje omota:\s*(.*?)\n', tekst, re.I)
        
        if m_ploca: medij = ocisti_stanje(m_ploca.group(1).strip())
        if m_omot: omot = ocisti_stanje(m_omot.group(1).strip())
        
        # STANJE IZ KRATKOG OPISA KAO FALLBACK
        if medij == "VG+" or omot == "VG+":
            kratki = soup.select_one(".product-short-description")
            if kratki:
                k_tekst = kratki.get_text(" ", strip=True)
                m_k_ploca = re.search(r'ploča\s+([A-Za-z0-9\+\-]+)', k_tekst, re.I)
                m_k_omot = re.search(r'omot\s+([A-Za-z0-9\+\-]+)', k_tekst, re.I)
                if m_k_ploca: medij = ocisti_stanje(m_k_ploca.group(1).strip())
                if m_k_omot: omot = ocisti_stanje(m_k_omot.group(1).strip())
    except Exception:
        pass
        
    return img_url, medij, omot

def izvuci_cijenu(tekst):
    svi_brojevi = re.findall(r'(\d{1,4}[.,]\d{2})\s*€', tekst)
    if not svi_brojevi:
        svi_brojevi = re.findall(r'(\d{1,4}[.,]\d{2})', tekst)
    if svi_brojevi:
        dijelovi = svi_brojevi[-1].replace(',', '.').split('.')
        if len(dijelovi) == 2:
            try:
                val = float(f"{dijelovi[0]}.{dijelovi[1]}")
                if val > 0: return f"{val:.2f}"
            except: pass
    return "0.00"

print("ANALOGNI ZVUK: Pokrećem hibridni API + HTML sistem s CURL CFFI (SSL Fix)...", flush=True)

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

cffi_sess = cffi_requests.Session(impersonate="chrome120")
cffi_sess.headers.update(HEADERS)

dohvati_url("https://analogni-zvuk.hr/", cffi_sess, timeout=25)
time.sleep(1)

# === 1. WOOCOMMERCE STORE API ===
page = 1
api_radi = False

while True:
    api_url = f"https://analogni-zvuk.hr/wp-json/wc/store/products?page={page}&per_page=100"
    res = dohvati_url(api_url, cffi_sess, timeout=30)

    if res is None or res.status_code != 200 or not res.text.strip().startswith('['):
        break
    try:
        data = res.json()
        if not data:
            uspjesno_skenirano = True
            break
    except Exception:
        break

    api_radi = True
    dodano = 0

    for item in data:
        if not item.get('is_in_stock', False): continue
        kategorije_slugs = " ".join([k.get('slug', '').lower() for k in item.get('categories', [])])
        if any(zabranjeno in kategorije_slugs for zabranjeno in ['cd', 'kaset', 'oprema']):
            if not any(d in kategorije_slugs for d in ['ploc', 'vinil', 'lp', 'singl']): continue

        title = html.unescape(item.get('name', '')).strip()
        link = normaliziraj_link_proizvoda(item.get('permalink', ''))
        
        try:
            price = f"{float(item.get('prices', {}).get('price', '0')) / (10 ** 2):.2f}"
        except:
            price = "0.00"

        if price == "0.00": continue

        images = item.get('images', [])
        img_url = ocisti_url_slike(images[0].get('src', ''), is_srcset=False) if images else ""

        stanje_medija = "VG+"
        stanje_omota = "VG+"
        
        for attr in item.get('attributes', []):
            aname = attr.get('name', '').lower()
            tval = attr.get('terms', [])[0].get('name', '') if attr.get('terms') else ''
            if tval:
                if 'ploč' in aname or 'medij' in aname or 'stanj' in aname:
                    stanje_medija = ocisti_stanje(tval)
                if 'omot' in aname:
                    stanje_omota = ocisti_stanje(tval)

        sve_ploce[link] = [title, price, link, img_url, stanje_medija, stanje_omota, "Vinil"]
        vidjeni_linkovi.add(link)
        dodano += 1

    print(f"API Paket {page} -> OK ({dodano} ploča)", flush=True)
    page += 1
    time.sleep(0.5)

# === 2. FALLBACK NA HTML (AKO API NE RADI) ===
if not api_radi:
    print("\n--- Pokrećem HTML skeniranje kataloga sa Deep Inspectionom ---", flush=True)
    current_page_num = 1
    while True:
        cat_url = "https://analogni-zvuk.hr/product-category/gramofonske-ploce/"
        if current_page_num > 1: cat_url += f"page/{current_page_num}/"

        response = dohvati_url(cat_url, cffi_sess, timeout=30)
        if not response or response.status_code != 200:
            if response and response.status_code == 404: uspjesno_skenirano = True
            break

        soup = BeautifulSoup(response.text, 'lxml')
        products = soup.select('li.product, div.product, .type-product')

        if not products:
            uspjesno_skenirano = True
            break

        dodano = 0
        for prod in products:
            if 'outofstock' in " ".join(prod.get('class', [])).lower(): continue
            a_tag = prod.find('a', class_='woocommerce-LoopProduct-link') or prod.find('a', href=True)
            if not a_tag: continue
            
            link = normaliziraj_link_proizvoda(a_tag['href'])
            title_el = prod.select_one('.woocommerce-loop-product__title, .product-title, h2, h3')
            title = html.unescape(title_el.get_text(" ", strip=True)).strip() if title_el else ""
            bdi = prod.select_one('.price ins bdi, .price bdi, .price')
            price = izvuci_cijenu(bdi.get_text(" ", strip=True) if bdi else prod.get_text(" ", strip=True))
            
            if not title or price == "0.00": continue

            deep_img, deep_medij, deep_omot = dohvati_detalje_sa_stranice_proizvoda(link, cffi_sess)

            sve_ploce[link] = [title, price, link, deep_img, deep_medij, deep_omot, "Vinil"]
            vidjeni_linkovi.add(link)
            dodano += 1

        print(f"-> Stranica {current_page_num} OK ({dodano} ploča)", flush=True)
        current_page_num += 1
        time.sleep(0.8)

# === 3. SIGURNO BRISANJE I SPREMANJE ===
if uspjesno_skenirano and len(vidjeni_linkovi) > 20:
    pocetni = len(sve_ploce)
    sve_ploce = {k: v for k, v in sve_ploce.items() if k in vidjeni_linkovi}
    print(f"\n🧹 Sinkronizacija prodanih ploča uspješna (Obrisano: {pocetni - len(sve_ploce)}).", flush=True)

if sve_ploce:
    with open(CSV_FILENAME, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Naslov', 'Cijena', 'URL_Proizvoda', 'URL_Slike', 'Stanje_Medija', 'Stanje_Omota', 'Tip_Artikla'])
        writer.writerows(sve_ploce.values())
    print(f"🎉 Gotovo! Ukupno u CSV-u: {len(sve_ploce)}", flush=True)
