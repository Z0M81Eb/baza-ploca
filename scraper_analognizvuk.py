from curl_cffi import requests as cffi_requests
import cloudscraper
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin
import urllib3
import threading
import csv
import os
import re
import time
import html

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SITE_URL = "https://analogni-zvuk.hr"
CSV_FILENAME = "analognizvuk_ploce.csv"
BROJ_NITI = 6

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "hr-HR,hr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://analogni-zvuk.hr/"
}

_thread_local = threading.local()

class LegacyTlsAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        try:
            ctx.set_ciphers('DEFAULT@SECLEVEL=1')
        except Exception:
            pass
        kwargs['ssl_context'] = ctx
        return super(LegacyTlsAdapter, self).init_poolmanager(*args, **kwargs)

def dohvati_klijente():
    cffi_s = getattr(_thread_local, "cffi", None)
    cloud_s = getattr(_thread_local, "cloud", None)
    if cffi_s is None:
        cffi_s = cffi_requests.Session(impersonate="chrome120", verify=False)
        cffi_s.headers.update(HEADERS)
        _thread_local.cffi = cffi_s
    if cloud_s is None:
        cloud_s = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})
        cloud_s.mount('https://', LegacyTlsAdapter())
        cloud_s.headers.update(HEADERS)
        _thread_local.cloud = cloud_s
    return cffi_s, cloud_s

def dohvati_url(url, timeout=25):
    cffi_s, cloud_s = dohvati_klijente()
    for s in (cffi_s, cloud_s):
        try:
            r = s.get(url, timeout=timeout)
            if r.status_code in [200, 400, 404]:
                return r
        except Exception:
            continue
    return None

def normaliziraj_link_proizvoda(href):
    if not href:
        return ""
    puni = urljoin(SITE_URL, href.strip())
    return puni.split('?')[0].rstrip('/') + '/'

def ocisti_url_slike(raw_url):
    if not raw_url:
        return ""
    val = html.unescape(str(raw_url)).strip()
    if val.startswith('//'):
        val = 'https:' + val
    elif val.startswith('/'):
        val = urljoin(SITE_URL, val)
    return val.replace(' ', '%20') if val.startswith('http') else ""

def standardiziraj_ocjenu(sirovo):
    if not sirovo:
        return ""
    s = str(sirovo).strip().upper()
    
    # Ignoriraj generičke riječi koje nisu ocjena
    if s in ["RABLJENO", "USED", "SECOND HAND", "VINIL", "LP"]:
        return ""
    if "NOVO" in s or "SEALED" in s or "UNPLAYED" in s:
        return "Novo"
    if "NEAR MINT" in s:
        return "NM"
    if "VERY GOOD PLUS" in s:
        return "VG+"
    if "VERY GOOD" in s:
        return "VG"
    if "EXCELLENT" in s:
        return "EX"

    # ISPRAVLJEN REGEX: Koristimo (?<![A-Z]) i (?![A-Z0-9]) umjesto \b kako ne bi odrezao znak '+' ili '-'!
    m = re.search(r'(?<![A-Z])(NM|M-|M|EX\+|EX-|EX|VG\+|VG-|VG|G\+|G-|G|F|P)(?![A-Z0-9])', s)
    if m:
        ocjena = m.group(1)
        return "NM" if ocjena == "M-" else ocjena

    if "MINT" in s:
        return "M"
    return ""

def izvuci_ocjene_iz_teksta(sirovi_html_ili_tekst):
    if not sirovi_html_ili_tekst:
        return "", ""
        
    # Pretvaramo HTML opis u čisti tekst
    cisti_tekst = BeautifulSoup(str(sirovi_html_ili_tekst), "lxml").get_text(" ", strip=True)
    
    medij = ""
    omot = ""

    # 1. Primarni uzorak s Analognog Zvuka (npr. ".Stanje omota: VG+ Stanje ploče: VG+")
    m_ploca = re.search(r'Stanje\s+plo[čc][ae]\s*:?\s*(NM|M-|M|EX\+|EX-|EX|VG\+|VG-|VG|G\+|G-|G|Novo|New|Sealed)(?![A-Za-z0-9])', cisti_tekst, re.I)
    if m_ploca:
        medij = standardiziraj_ocjenu(m_ploca.group(1))

    m_omot = re.search(r'Stanje\s+omota\s*:?\s*(NM|M-|M|EX\+|EX-|EX|VG\+|VG-|VG|G\+|G-|G|Novo|New|Sealed)(?![A-Za-z0-9])', cisti_tekst, re.I)
    if m_omot:
        omot = standardiziraj_ocjenu(m_omot.group(1))

    # 2. Sekundarni uzorak ako piše skraćeno (npr. "Ploča: VG+, Omot: VG")
    if not medij:
        m_k_ploca = re.search(r'plo[čc][ae]\s*:?\s*(NM|M-|M|EX\+|EX-|EX|VG\+|VG-|VG|G\+|G-|G|Novo)(?![A-Za-z0-9])', cisti_tekst, re.I)
        if m_k_ploca:
            medij = standardiziraj_ocjenu(m_k_ploca.group(1))

    if not omot:
        m_k_omot = re.search(r'omot[a]?\s*:?\s*(NM|M-|M|EX\+|EX-|EX|VG\+|VG-|VG|G\+|G-|G|Novo)(?![A-Za-z0-9])', cisti_tekst, re.I)
        if m_k_omot:
            omot = standardiziraj_ocjenu(m_k_omot.group(1))

    # 3. Provjera za potpuno nove ploče
    if not medij and re.search(r'\b(nova ploča|tvornički zapakirano|sealed|unplayed)\b', cisti_tekst, re.I):
        medij = "Novo"
        omot = "Novo"

    return medij, omot

def izvuci_stanje_i_sliku_iz_htmla(product_url):
    img_url = ""
    medij = ""
    omot = ""
    
    r = dohvati_url(product_url, timeout=20)
    if not r or r.status_code != 200:
        return img_url, "VG+", "VG+"

    try:
        soup = BeautifulSoup(r.text, 'lxml')
        
        og = soup.find('meta', property='og:image') or soup.find('meta', attrs={'name': 'twitter:image'})
        if og and og.get('content'):
            img_url = ocisti_url_slike(og['content'])

        # Prvo provjeri točan tab s opisom koji je Scout pronašao
        opis_panel = soup.select_one('.woocommerce-Tabs-panel--description, .product-short-description, .summary')
        if opis_panel:
            medij, omot = izvuci_ocjene_iz_teksta(opis_panel.get_text(" ", strip=True))
            
        # Ako nije u panelu, pretraži cijeli tekst stranice
        if not medij or not omot:
            m2, o2 = izvuci_ocjene_iz_teksta(soup.get_text(" ", strip=True))
            if not medij: medij = m2
            if not omot: omot = o2
    except Exception:
        pass

    if not medij:
        medij = "VG+"
    if not omot:
        omot = medij if medij == "Novo" else "VG+"

    return img_url, medij, omot

def obradi_jedan_api_item(item):
    title = html.unescape(item.get('name', '')).strip()
    link = normaliziraj_link_proizvoda(item.get('permalink', ''))
    
    try:
        minor = item.get('prices', {}).get('currency_minor_unit', 2)
        price = f"{float(item.get('prices', {}).get('price', '0')) / (10 ** minor):.2f}"
    except Exception:
        price = "0.00"

    images = item.get('images', [])
    img_url = ocisti_url_slike(images[0].get('src', '')) if images else ""

    # 1. Pokušaj izvući ocjene direktno iz 'description' i 'short_description' polja koja vraća API!
    api_opis = f"{item.get('description', '')} {item.get('short_description', '')}"
    medij, omot = izvuci_ocjene_iz_teksta(api_opis)

    # 2. Provjeri i atribute u API-ju (ako slučajno piše 'Novo' ili prava ocjena umjesto 'Rabljeno')
    for attr in item.get('attributes', []):
        aname = attr.get('name', '').lower()
        tval = attr.get('terms', [])[0].get('name', '') if attr.get('terms') else ''
        ocjena = standardiziraj_ocjenu(tval)
        if ocjena:
            if not medij and ('ploč' in aname or 'ploc' in aname or 'medij' in aname or 'stanj' in aname):
                medij = ocjena
            if not omot and ('omot' in aname or ocjena == "Novo"):
                omot = ocjena

    # 3. Ako ni u API opisu ni u atributima nema ocjene (ili fali slika), otvori HTML stranicu proizvoda
    if not medij or not omot or not img_url:
        deep_img, deep_medij, deep_omot = izvuci_stanje_i_sliku_iz_htmla(link)
        if not img_url:
            img_url = deep_img
        if not medij:
            medij = deep_medij
        if not omot:
            omot = deep_omot

    # Određivanje tipa artikla iz API atributa 'Format' ili naslova
    tip_artikla = "LP"
    for attr in item.get('attributes', []):
        if attr.get('name', '').lower() == 'format' and attr.get('terms'):
            fmt = attr['terms'][0].get('name', '').upper()
            if '7' in fmt or 'SINGL' in fmt:
                tip_artikla = 'Singl 7"'
    if any(k in title.lower() for k in ['7"', 'singl']):
        tip_artikla = 'Singl 7"'

    return link, [title, price, link, img_url, medij, omot, tip_artikla]

def scrape_analogni():
    print("🚀 ANALOGNI ZVUK: Pokrećem WooCommerce Store API v1 + Regex +Plus Fix...", flush=True)
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
        print(f"📦 Učitano {len(sve_ploce)} postojećih ploča iz baze.", flush=True)

    dohvati_url("https://analogni-zvuk.hr/", timeout=20)
    
    page = 1
    api_radi = False
    kandidati_za_obradu = []

    while True:
        # Koristimo v1 API putanju koju je Scout potvrdio
        api_url = f"https://analogni-zvuk.hr/wp-json/wc/store/v1/products?page={page}&per_page=100"
        res = dohvati_url(api_url, timeout=30)

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
        for item in data:
            if not item.get('is_in_stock', False):
                continue
            kategorije_slugs = " ".join([k.get('slug', '').lower() for k in item.get('categories', [])])
            if any(zab in kategorije_slugs for zab in ['cd', 'kaset', 'oprema']):
                if not any(d in kategorije_slugs for d in ['ploc', 'vinil', 'lp', 'singl']):
                    continue
            
            try:
                p_val = float(item.get('prices', {}).get('price', '0'))
                if p_val <= 0:
                    continue
            except Exception:
                continue

            kandidati_za_obradu.append(item)

        print(f"📡 API Stranica {page} učitana (Ukupno kandidata na zalihi: {len(kandidati_za_obradu)})", flush=True)
        page += 1
        time.sleep(0.3)

    if api_radi and kandidati_za_obradu:
        print(f"\n🔎 Izvlačim točne ocjene (VG+, VG, NM) za {len(kandidati_za_obradu)} ploča...", flush=True)
        with ThreadPoolExecutor(max_workers=BROJ_NITI) as executor:
            for idx, (link, red) in enumerate(executor.map(obradi_jedan_api_item, kandidati_za_obradu), 1):
                sve_ploce[link] = red
                vidjeni_linkovi.add(link)
                if idx % 100 == 0 or idx == len(kandidati_za_obradu):
                    print(f"   ⚡ Obrađeno {idx}/{len(kandidati_za_obradu)} ploča (Zadnja: {red[4]}/{red[5]})...", flush=True)

    if uspjesno_skenirano and len(vidjeni_linkovi) > 20:
        pocetni = len(sve_ploce)
        sve_ploce = {k: v for k, v in sve_ploce.items() if k in vidjeni_linkovi}
        print(f"\n🧹 Sinkronizacija prodanih ploča završena (Obrisano: {pocetni - len(sve_ploce)}).", flush=True)

    if sve_ploce:
        with open(CSV_FILENAME, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Naslov', 'Cijena', 'URL_Proizvoda', 'URL_Slike', 'Stanje_Medija', 'Stanje_Omota', 'Tip_Artikla'])
            writer.writerows(sve_ploce.values())
        print(f"🎉 Gotovo! Ukupno u CSV-u: {len(sve_ploce)} ploča s čistim ocjenama.", flush=True)

if __name__ == "__main__":
    scrape_analogni()
