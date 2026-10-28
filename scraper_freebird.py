import cloudscraper
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

SITE_URL = "https://freebird.hr"
CSV_FILENAME = "freebird_ploce.csv"
BROJ_NITI = 6
LIMIT_PO_STRANICI = 100

ZADANE_VINIL_KATEGORIJE = [
    ("LP", "https://freebird.hr/index.php?route=product/category&path=225_223", "LP"),
    ('7"', "https://freebird.hr/index.php?route=product/category&path=225_233", 'Singl 7"'),
    ('12" SINGLE', "https://freebird.hr/index.php?route=product/category&path=225_301", "LP"),
    ('10"', "https://freebird.hr/index.php?route=product/category&path=225_235", "LP"),
]

_thread_local = threading.local()

def kreiraj_sesiju():
    s = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )
    s.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
        "Referer": SITE_URL + "/",
        "Connection": "keep-alive"
    })
    return s

def dohvati_sesiju():
    s = getattr(_thread_local, "session", None)
    if s is None:
        s = kreiraj_sesiju()
        _thread_local.session = s
    return s

def sigurni_get(url, timeout=30):
    for pokusaj in range(1, 5):
        try:
            s = dohvati_sesiju()
            r = s.get(url, timeout=timeout)
            if r.status_code == 200 and len(r.text) > 2000:
                return r.text
            time.sleep(1.5 * pokusaj)
            _thread_local.session = kreiraj_sesiju()
        except Exception as e:
            time.sleep(1.5 * pokusaj)
            _thread_local.session = kreiraj_sesiju()
    return None

def otkrij_vinil_kategorije():
    print("🔎 Automatski otkrivam aktivne vinil kategorije...", flush=True)
    html_tekst = sigurni_get(SITE_URL)
    if not html_tekst:
        return ZADANE_VINIL_KATEGORIJE
    soup = BeautifulSoup(html_tekst, "lxml")
    otkrivene = []
    vidjeni_pathovi = set()
    for a in soup.select('a[href*="route=product/category"]'):
        href = html.unescape(a.get("href", "")).strip()
        tekst = " ".join(a.get_text(" ", strip=True).split())
        m_path = re.search(r"path=(225_\d+)", href)
        if not m_path: continue
        path_val = m_path.group(1)
        if path_val in vidjeni_pathovi: continue
        m_broj = re.search(r"\s+(\d+)$", tekst)
        if m_broj:
            if int(m_broj.group(1)) == 0: continue
            cisti_naziv = tekst[:m_broj.start()].strip()
        else:
            cisti_naziv = tekst
        vidjeni_pathovi.add(path_val)
        cisti_url = f"{SITE_URL}/index.php?route=product/category&path={path_val}"
        tip = 'Singl 7"' if any(o in cisti_naziv.upper() for o in ['7"', 'EP']) else "LP"
        otkrivene.append((cisti_naziv, cisti_url, tip))
    return otkrivene if otkrivene else ZADANE_VINIL_KATEGORIJE

def normaliziraj_link_proizvoda(href):
    puni = urljoin(SITE_URL, html.unescape(str(href)).strip())
    m_id = re.search(r"product_id=(\d+)", puni)
    if m_id:
        return f"{SITE_URL}/index.php?route=product/product&product_id={m_id.group(1)}"
    return puni.split("&limit=")[0].split("&page=")[0].rstrip("/")

def izvuci_stanja_iz_opisa(opis_tekst):
    medij = "VG+"
    omot = "VG+"
    if not opis_tekst:
        return medij, omot
        
    m_rec = re.search(r'Condition records:\s*([A-Za-z0-9\+\-]+)', opis_tekst, re.I)
    if m_rec:
        medij = m_rec.group(1).strip()
        
    m_slv = re.search(r'Condition sleeve:\s*([A-Za-z0-9\+\-]+)', opis_tekst, re.I)
    if m_slv:
        omot = m_slv.group(1).strip()
        
    return medij, omot

def parsiraj_stranicu_kataloga(html_tekst, zadani_tip):
    soup = BeautifulSoup(html_tekst, "lxml")
    stavke = []
    kartice = soup.select("div.product-thumb")
    vidjeni = set()

    for k in kartice:
        if "out-of-stock" in " ".join(k.get("class", [])).lower() or not k.select_one("a.btn-cart"):
            continue
        a_name = k.select_one("div.name a[href]") or k.select_one("a.product-img[href]")
        if not a_name: continue
        link = normaliziraj_link_proizvoda(a_name.get("href", ""))
        if not link or link in vidjeni: continue
        
        naslov = re.sub(r"\s+", " ", html.unescape(a_name.get_text(" ", strip=True))).strip()
        
        el_cijena = k.select_one("span.price-new, span.price-normal, div.price")
        if not el_cijena: continue
        cijena_tekst = el_cijena.get_text(" ", strip=True).split("Ex Tax")[0].replace(",", ".")
        m_cijena = re.search(r"(\d+(?:\.\d{1,2})?)", cijena_tekst)
        if not m_cijena or float(m_cijena.group(1)) <= 0: continue
        cijena = f"{float(m_cijena.group(1)):.2f}"
        
        img_el = k.select_one("img")
        slika_url = urljoin(SITE_URL, img_el.get("src", "").strip()) if img_el else ""
        
        opis_el = k.select_one("div.description")
        opis_tekst = html.unescape(opis_el.get_text(" ", strip=True)) if opis_el else ""
        stanje_medija, stanje_omota = izvuci_stanja_iz_opisa(opis_tekst)
        
        vidjeni.add(link)
        red = [naslov, cijena, link, slika_url, stanje_medija, stanje_omota, zadani_tip]
        otisak = (naslov.lower(), cijena, slika_url, stanje_medija.lower())
        stavke.append((link, red, otisak))

    max_str = 1
    for a in soup.select("ul.pagination a[href]"):
        m_p = re.search(r"[?&]page=(\d+)", a["href"])
        if m_p and int(m_p.group(1)) > max_str:
            max_str = int(m_p.group(1))
            
    return stavke, len(kartice), max_str

def obradi_stranicu_zadatka(args):
    baza_url, page, zadani_tip = args
    url = f"{baza_url}&limit={LIMIT_PO_STRANICI}{'&page='+str(page) if page > 1 else ''}"
    html_tekst = sigurni_get(url)
    if not html_tekst:
        return page, False, [], 0, 1
    stavke, br_kartica, max_str = parsiraj_stranicu_kataloga(html_tekst, zadani_tip)
    return page, True, stavke, br_kartica, max_str

def scrape_freebird():
    sve_ploce, aktivni_linkovi, mapa_otisaka = {}, set(), {}
    if os.path.exists(CSV_FILENAME):
        with open(CSV_FILENAME, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) >= 7:
                    sve_ploce[normaliziraj_link_proizvoda(row[2])] = row

    kategorije = otkrij_vinil_kategorije()
    with ThreadPoolExecutor(max_workers=BROJ_NITI) as executor:
        for idx_kat, (naziv_kat, kat_url, tip_artikla) in enumerate(kategorije, 1):
            print(f"\n📂 Analiziram: '{naziv_kat}'", flush=True)
            _, ok1, stavke1, br_kartica1, max_pages = obradi_stranicu_zadatka((kat_url, 1, tip_artikla))
            
            for link, red, otisak in stavke1:
                sve_ploce[link] = red
                aktivni_linkovi.add(link)
                
            if max_pages > 1:
                for i in range(2, max_pages + 1, BROJ_NITI):
                    blok = list(range(i, min(i + BROJ_NITI, max_pages + 1)))
                    zadaci = [(kat_url, p, tip_artikla) for p in blok]
                    for page, ok, stavke, _, _ in executor.map(obradi_stranicu_zadatka, zadaci):
                        if ok:
                            for link, red, otisak in stavke:
                                sve_ploce[link] = red
                                aktivni_linkovi.add(link)

    if len(aktivni_linkovi) > 1000:
        sve_ploce = {k: v for k, v in sve_ploce.items() if k in aktivni_linkovi}
        
    if sve_ploce:
        with open(CSV_FILENAME, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Naslov", "Cijena", "URL_Proizvoda", "URL_Slike", "Stanje_Medija", "Stanje_Omota", "Tip_Artikla"])
            writer.writerows(sve_ploce.values())
        print(f"🎉 GOTOVO! U bazi je {len(sve_ploce)} čistih Freebird vinila.", flush=True)

if __name__ == "__main__":
    scrape_freebird()
