from curl_cffi import requests as cffi_requests
import cloudscraper
from bs4 import BeautifulSoup
import csv
import os
import re
import time
import html
from urllib.parse import urljoin, quote

SITE_URL = "https://ezop-antikvarijat.hr"
TRANSLATE_HOST = "ezop--antikvarijat-hr.translate.goog"
CSV_FILENAME = "ezop_ploce.csv"

HTML_ARCHIVES = [
    "https://ezop-antikvarijat.hr/kategorija/glazba/"
]

API_ENDPOINTS = [
    ("https://ezop-antikvarijat.hr/wp-json/wc/store/products", "?"),
    ("https://ezop-antikvarijat.hr/wp-json/wc/store/v1/products", "?"),
    ("https://ezop-antikvarijat.hr/?rest_route=/wc/store/products", "&")
]

# Vanjski proxy relayi za zaobilaženje blokade GitHub (Microsoft Azure) IP adresa
PROXY_RELAYS = [
    ("CodeTabs", lambda u: f"https://api.codetabs.com/v1/proxy?quest={quote(u, safe='')}"),
    ("AllOrigins", lambda u: f"https://api.allorigins.win/raw?url={quote(u, safe='')}"),
    ("CorsProxy", lambda u: f"https://corsproxy.io/?url={quote(u, safe='')}")
]

def normaliziraj_link_proizvoda(href):
    if not href:
        return ""
    cisti = str(href).strip().replace(TRANSLATE_HOST, "ezop-antikvarijat.hr")
    puni = urljoin(SITE_URL, cisti)
    return puni.split('?')[0].rstrip('/') + '/'

def pretvori_ocjenu(ocjena_str):
    if not ocjena_str:
        return ""
    tekst = str(ocjena_str).strip()
    tekst_upper = tekst.upper()
    if tekst_upper in ["M", "NM", "VG+", "VG", "G+", "G", "F", "P", "NOVO"]:
        return tekst_upper
    if "RABLJENO" in tekst_upper:
        return "VG+"
    try:
        ocjena = int(float(tekst))
        mape = {10: "M", 9: "NM", 8: "VG+", 7: "VG", 6: "G+", 5: "G"}
        return mape.get(ocjena, "VG")
    except Exception:
        return tekst_upper

def ocisti_url_slike(raw_url, is_srcset=False):
    if not raw_url:
        return ""
    val = html.unescape(str(raw_url)).strip()
    val = val.replace(TRANSLATE_HOST, "ezop-antikvarijat.hr")
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

    if '?_x_tr_' in val:
        val = val.split('?_x_tr_')[0]

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

def broj_razlicitih_proizvoda_u_elementu(el):
    linkovi = set()
    for a in el.find_all('a', href=True):
        h = a['href'].strip()
        if '/proizvod/' in h and '?add-to-cart=' not in h:
            linkovi.add(normaliziraj_link_proizvoda(h))
            if len(linkovi) > 1:
                return len(linkovi)
    return len(linkovi)

def pronadji_puni_okvir_kartice(start_el):
    kandidat = start_el
    for _ in range(7):
        roditelj = kandidat.parent
        if not roditelj or getattr(roditelj, 'name', None) in ['body', 'main', 'html', '[document]']:
            break
        if broj_razlicitih_proizvoda_u_elementu(roditelj) == 1:
            kandidat = roditelj
        else:
            break
    return kandidat

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

def ispisi_dijagnostiku_403(res, izvor="Direktno"):
    if res is None:
        print(f"⚠️ [{izvor}] Nema odgovora sa servera.", flush=True)
        return
    cf_mit = res.headers.get('cf-mitigated', 'nema')
    server = res.headers.get('Server', 'Nepoznat')
    title_m = re.search(r'<title[^>]*>(.*?)</title>', res.text, re.IGNORECASE | re.DOTALL)
    title = title_m.group(1).strip() if title_m else "Bez naslova"
    uzorak = re.sub(r'\s+', ' ', res.text[:220]).strip()
    print(f"📡 [{izvor}] HTTP {res.status_code} | Server: {server} | cf-mitigated: {cf_mit} | Title: '{title}'", flush=True)
    if res.status_code != 200:
        print(f"📄 [{izvor}] Uzorak odgovora: {uzorak}", flush=True)

class EzopFetcher:
    def __init__(self):
        # VAŽNO: Ne gazimo defaultne headere u curl_cffi kako ne bismo srušili Chrome TLS/HTTP2 fingerprint!
        self.cffi_sessions = {}
        for p in ["chrome120", "chrome116", "safari15_5"]:
            try:
                self.cffi_sessions[p] = cffi_requests.Session(impersonate=p)
            except Exception:
                pass
        self.cloud_s = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})
        self.aktivni_api_relay = None
        self.koristi_translate_html = False
        self.dijagnostika_ispisana = False

    def dohvati(self, url, is_json=False, timeout=25):
        # 1. Ako smo već otkrili da samo proxy relay radi za JSON, koristi njega prvo
        if is_json and self.aktivni_api_relay is not None:
            ime_relaya, fn_relaya = PROXY_RELAYS[self.aktivni_api_relay]
            try:
                p_url = fn_relaya(url)
                s = next(iter(self.cffi_sessions.values()))
                r = s.get(p_url, timeout=timeout)
                if r.status_code in [200, 400, 404]:
                    return r
            except Exception:
                self.aktivni_api_relay = None

        # 2. Pokušaj direktno s čistim curl_cffi profilima (bez custom HEADERS)
        zadnji_res = None
        for profil, sess in self.cffi_sessions.items():
            try:
                res = sess.get(url, timeout=timeout)
                zadnji_res = res
                if res.status_code == 200:
                    if is_json and not (res.text.strip().startswith('[') or res.text.strip().startswith('{')):
                        continue
                    return res
                elif res.status_code in [400, 404]:
                    return res
            except Exception:
                continue

        if not self.dijagnostika_ispisana and zadnji_res is not None:
            ispisi_dijagnostiku_403(zadnji_res, "Direktni curl_cffi")
            self.dijagnostika_ispisana = True

        # 3. Pokušaj kroz proxy relaye (zaobilazi blokadu GitHub Azure IP adresa)
        for idx, (ime_relaya, fn_relaya) in enumerate(PROXY_RELAYS):
            try:
                p_url = fn_relaya(url)
                s = next(iter(self.cffi_sessions.values()))
                res = s.get(p_url, timeout=timeout)
                if res.status_code == 200:
                    tekst = res.text.strip()
                    if is_json and (tekst.startswith('[') or tekst.startswith('{')):
                        if self.aktivni_api_relay != idx:
                            print(f"\n🔓 Probijena blokada preko [{ime_relaya}] proxyja!", flush=True)
                            self.aktivni_api_relay = idx
                        return res
                    elif not is_json and ('/proizvod/' in tekst or 'woocommerce' in tekst.lower()):
                        print(f"\n🔓 HTML dohvaćen preko [{ime_relaya}] proxyja!", flush=True)
                        return res
            except Exception:
                continue

        # 4. Ako tražimo HTML, pokušaj preko Google Translate proxy mosta (Google IP adrese)
        if not is_json:
            try:
                putanja = url.replace(SITE_URL, "")
                sep = "&" if "?" in putanja else "?"
                g_url = f"https://{TRANSLATE_HOST}{putanja}{sep}_x_tr_sl=en&_x_tr_tl=hr&_x_tr_hl=hr"
                s = next(iter(self.cffi_sessions.values()))
                res = s.get(g_url, timeout=timeout)
                if res.status_code == 200 and ('/proizvod/' in res.text or 'product' in res.text.lower()):
                    if not self.koristi_translate_html:
                        print("\n🔓 HTML probijen preko Google Translate Proxyja!", flush=True)
                        self.koristi_translate_html = True
                    return res
                elif res.status_code == 404:
                    return res
            except Exception:
                pass

        return None

def get_glazba_category_id(fetcher):
    print("🔍 Tražim ID kategorije 'Glazba'...", flush=True)
    for url in [
        "https://ezop-antikvarijat.hr/wp-json/wc/store/products/categories?per_page=100",
        "https://ezop-antikvarijat.hr/?rest_route=/wc/store/products/categories&per_page=100"
    ]:
        res = fetcher.dohvati(url, is_json=True, timeout=25)
        if res and res.status_code == 200:
            try:
                podaci = res.json()
                if isinstance(podaci, list):
                    for cat in podaci:
                        slug = cat.get('slug', '').lower()
                        link = cat.get('link', '').lower()
                        if slug == 'glazba' or '/kategorija/glazba' in link:
                            print(f"✅ Pronađena kategorija Glazba (ID: {cat['id']})", flush=True)
                            return cat['id']
            except Exception:
                continue
    print("⚠️ ID kategorije nije dohvaćen preko API-ja, nastavljam...", flush=True)
    return None

def je_li_nevinil(tekst):
    t = tekst.lower()
    zabranjeno = [' cd', '(cd)', 'cd-', 'audio cd', 'kazet', 'kaset', 'knjig', 'strip', 'dvd', 'blu-ray', 'vhs']
    return any(z in t for z in zabranjeno)

def scrape_ezop():
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
        print(f"📦 Učitano {len(sve_ploce)} postojećih ploča iz memorije.", flush=True)

    fetcher = EzopFetcher()
    cat_id = get_glazba_category_id(fetcher)

    # === 1. POKUŠAJ PREKO API-JA ===
    print("\n=== KORAK 1: POKREĆEM EZOP API SKENIRANJE ===", flush=True)
    page = 1
    per_page = 100
    api_uspio = False

    while True:
        print(f"Preuzimam API paket {page}...", end=" ", flush=True)
        res = None
        for base_url, sep in API_ENDPOINTS:
            url = f"{base_url}{sep}category={cat_id}&page={page}&per_page={per_page}" if cat_id else f"{base_url}{sep}page={page}&per_page={per_page}"
            res = fetcher.dohvati(url, is_json=True, timeout=30)
            if res and res.status_code in [200, 400, 404]:
                break

        if res is None or res.status_code not in [200, 400, 404]:
            print("-> API blokiran ili nedostupan. Prelazim na HTML skeniranje!", flush=True)
            break

        if res.status_code in [400, 404]:
            print("-> Kraj API arhive dostignut.", flush=True)
            if api_uspio:
                uspjesno_skenirano = True
            break

        try:
            data = res.json()
        except Exception:
            print("-> Neispravan JSON. Prelazim na HTML skeniranje!", flush=True)
            break

        # Ako API preko proxyja vrati dict s greškom (npr. {"code": "rest_post_invalid_page_number"}), to je kraj arhive!
        if isinstance(data, dict):
            if 'code' in data and api_uspio:
                print(f"-> Kraj API arhive ({data.get('code')}).", flush=True)
                uspjesno_skenirano = True
            else:
                print("-> Neočekivan JSON objekt. Prelazim na HTML!", flush=True)
            break

        if not data or not isinstance(data, list):
            print("-> Prazan paket. Kraj API arhive.", flush=True)
            if api_uspio:
                uspjesno_skenirano = True
            break

        api_uspio = True
        broj_ploca_u_paketu = 0

        for item in data:
            if not item.get('is_in_stock', False):
                continue

            naslov = html.unescape(item.get('name', '')).strip()
            link = normaliziraj_link_proizvoda(item.get('permalink', ''))
            if not naslov or not link:
                continue

            is_vinyl = True if cat_id else False
            nije_vinil_medij = je_li_nevinil(naslov)
            s_omot = ""
            s_medij = ""

            for var in item.get('variations', []):
                for attr in var.get('attributes', []):
                    ime_attr = attr.get('name', '').lower()
                    vrijednost = str(attr.get('value', ''))
                    if je_li_nevinil(vrijednost):
                        nije_vinil_medij = True
                    if 'omot' in ime_attr:
                        s_omot = pretvori_ocjenu(vrijednost)
                        is_vinyl = True
                    elif any(k in ime_attr for k in ['medij', 'ploč', 'ploc', 'stanje']):
                        s_medij = pretvori_ocjenu(vrijednost)
                        is_vinyl = True

            for attr in item.get('attributes', []):
                ime_attr = attr.get('name', '').lower()
                terms = attr.get('terms', [])
                vrijednost = str(terms[0].get('name', '')) if terms else ''
                if je_li_nevinil(vrijednost):
                    nije_vinil_medij = True
                if 'omot' in ime_attr and not s_omot:
                    s_omot = pretvori_ocjenu(vrijednost)
                    is_vinyl = True
                elif any(k in ime_attr for k in ['medij', 'ploč', 'ploc', 'stanje']) and not s_medij:
                    s_medij = pretvori_ocjenu(vrijednost)
                    is_vinyl = True

            for k in item.get('categories', []):
                slug = k.get('slug', '').lower()
                name_kat = k.get('name', '').lower()
                link_kat = k.get('link', '').lower()
                sve_kat = f"{slug} {name_kat} {link_kat}"
                if any(w in sve_kat for w in ['cd', 'kazet', 'kaset', 'strip', 'knjig']):
                    nije_vinil_medij = True
                if any(w in sve_kat for w in ['gramofonske', 'ploce', 'ploče', 'vinil', 'vinyl', 'lp', 'singl', 'glazba']):
                    is_vinyl = True

            if nije_vinil_medij or not is_vinyl:
                continue

            if not s_medij:
                s_medij = "VG+"
            if not s_omot:
                s_omot = s_medij

            prices = item.get('prices', {})
            raw_price = prices.get('price', '0')
            minor_unit = prices.get('currency_minor_unit', 2)
            try:
                price_val = float(raw_price) / (10 ** minor_unit)
                cijena = f"{price_val:.2f}"
            except Exception:
                cijena = "0.00"

            if cijena == "0.00":
                continue

            images = item.get('images', [])
            slika_url = ocisti_url_slike(images[0].get('src', ''), is_srcset=False) if images else ""
            if not slika_url and link in sve_ploce and len(sve_ploce[link]) >= 4:
                slika_url = ocisti_url_slike(sve_ploce[link][3], is_srcset=False)

            sve_ploce[link] = [naslov, cijena, link, slika_url, s_medij, s_omot, "Vinil"]
            vidjeni_linkovi.add(link)
            broj_ploca_u_paketu += 1

        print(f"-> Pronađeno ploča: {broj_ploca_u_paketu}", flush=True)
        page += 1
        time.sleep(0.6)

    # === 2. HTML FALLBACK AKO JE API BLOKIRAN ILI VRATIO PREMALO PLOČA ===
    if not uspjesno_skenirano or len(vidjeni_linkovi) < 20:
        print("\n=== KORAK 2: POKREĆEM HTML SKENIRANJE EZOP ARHIVE ===", flush=True)
        for base_url in HTML_ARCHIVES:
            page = 1
            praznih_zaredom = 0
            while True:
                url = base_url if page == 1 else f"{base_url.rstrip('/')}/page/{page}/"
                print(f"📄 HTML Stranica {page}...", end=" ", flush=True)

                res = fetcher.dohvati(url, is_json=False, timeout=25)
                if not res:
                    print("-> Greška spajanja na svim kanalima.", flush=True)
                    break
                if res.status_code == 404:
                    print("-> Kraj arhive (404).", flush=True)
                    if page > 1:
                        uspjesno_skenirano = True
                    break
                if res.status_code != 200:
                    print(f"-> Status {res.status_code}", flush=True)
                    break

                soup = BeautifulSoup(res.text, 'lxml')
                kartice = soup.select('li.product, div.product, article.product, .type-product')
                if not kartice:
                    vidjeni_okviri = set()
                    for a in soup.select('a[href*="/proizvod/"]'):
                        if '?add-to-cart=' in a.get('href', ''):
                            continue
                        okvir = pronadji_puni_okvir_kartice(a)
                        if okvir and id(okvir) not in vidjeni_okviri:
                            vidjeni_okviri.add(id(okvir))
                            kartice.append(okvir)

                if not kartice:
                    print("-> Nema više proizvoda. Kraj arhive.", flush=True)
                    if page > 1:
                        uspjesno_skenirano = True
                    break

                dodano_str = 0
                sa_slikom = 0
                vidjeni_na_str = set()

                for k in kartice:
                    puni_k = pronadji_puni_okvir_kartice(k)
                    klase = " ".join((k.get('class', []) or []) + (puni_k.get('class', []) or [])).lower()
                    tekst_k = puni_k.get_text(" ", strip=True)
                    if 'outofstock' in klase or 'rasprodano' in tekst_k.lower():
                        continue

                    link = ""
                    for a in puni_k.find_all('a', href=True):
                        h = a['href'].strip()
                        if '/proizvod/' in h and '?add-to-cart=' not in h:
                            link = normaliziraj_link_proizvoda(h)
                            break
                    if not link or link in vidjeni_na_str:
                        continue

                    naslov_el = puni_k.select_one('.woocommerce-loop-product__title, .product-title, h2, h3, h4')
                    naslov = html.unescape(naslov_el.get_text(" ", strip=True)).strip() if naslov_el else ""
                    if not naslov or je_li_nevinil(naslov):
                        continue

                    cijena_el = puni_k.select_one('.price ins, .price')
                    cijena = izvuci_cijenu(cijena_el.get_text(" ", strip=True) if cijena_el else tekst_k)
                    if cijena == "0.00":
                        continue

                    slika_url = izvuci_sliku_iz_elementa(puni_k)
                    if not slika_url and link in sve_ploce and len(sve_ploce[link]) >= 4:
                        slika_url = ocisti_url_slike(sve_ploce[link][3], is_srcset=False)

                    s_medij = "VG+"
                    s_omot = "VG+"
                    if link in sve_ploce and len(sve_ploce[link]) >= 6:
                        s_medij = sve_ploce[link][4] or "VG+"
                        s_omot = sve_ploce[link][5] or "VG+"

                    if slika_url:
                        sa_slikom += 1

                    vidjeni_na_str.add(link)
                    sve_ploce[link] = [naslov, cijena, link, slika_url, s_medij, s_omot, "Vinil"]
                    vidjeni_linkovi.add(link)
                    dodano_str += 1

                if dodano_str == 0:
                    praznih_zaredom += 1
                    print("-> 0 na zalihi na ovoj stranici.", flush=True)
                    if praznih_zaredom >= 5:
                        uspjesno_skenirano = True
                        break
                else:
                    praznih_zaredom = 0
                    print(f"-> Pronađeno {dodano_str} vinila (Sa slikom: {sa_slikom}/{dodano_str}).", flush=True)

                page += 1
                time.sleep(0.8)

    if uspjesno_skenirano and len(vidjeni_linkovi) > 50:
        pocetni_broj = len(sve_ploce)
        sve_ploce = {k: v for k, v in sve_ploce.items() if k in vidjeni_linkovi}
        obrisano = pocetni_broj - len(sve_ploce)
        print(f"\n🧹 Analiza završena. Obrisano {obrisano} prodanih ploča.", flush=True)
    else:
        print(f"\n⚠️ UPOZORENJE: Pronađeno {len(vidjeni_linkovi)} ploča. Preskačem brisanje starih ploča radi sigurnosti baze.", flush=True)

    if sve_ploce:
        with open(CSV_FILENAME, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Naslov', 'Cijena', 'URL_Proizvoda', 'URL_Slike', 'Stanje_Medija', 'Stanje_Omota', 'Tip_Artikla'])
            writer.writerows(sve_ploce.values())
        ukupno_slika = sum(1 for v in sve_ploce.values() if len(v) >= 4 and v[3])
        print(f"🎉 Završeno. U bazi je sada ukupno {len(sve_ploce)} ažurnih ploča (Sa slikom: {ukupno_slika}).", flush=True)

if __name__ == "__main__":
    scrape_ezop()
