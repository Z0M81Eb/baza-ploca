import cloudscraper
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor
import urllib3
import threading
import csv
import os
import re
import time
import html
from urllib.parse import urljoin

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SITE_URL = "https://cedeterijanova.hr"
CSV_FILENAME = "cedeterija_ploce.csv"
BROJ_NITI = 6

_thread_local = threading.local()

def kreiraj_sesiju():
    s = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'desktop': True
        }
    )
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "hr-HR,hr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": SITE_URL,
        "Connection": "keep-alive"
    })
    try:
        s.get(SITE_URL, timeout=15)
    except Exception:
        pass
    return s

def dohvati_sesiju():
    s = getattr(_thread_local, "session", None)
    if s is None:
        s = kreiraj_sesiju()
        _thread_local.session = s
    return s

def sigurni_get(url, timeout=25):
    for pokusaj in range(1, 4):
        try:
            s = dohvati_sesiju()
            r = s.get(url, timeout=timeout)
            if r.status_code == 200 and len(r.text) > 200:
                return r
            time.sleep(1.5 * pokusaj)
        except Exception:
            time.sleep(1.5 * pokusaj)
            _thread_local.session = kreiraj_sesiju()
    return None

def dohvati_podatke_iz_sitemapa():
    glavni_sitemap = "https://cedeterijanova.hr/xml/sitemap_3.xml"
    res = sigurni_get(glavni_sitemap)
    
    sitemap_datoteke = []
    if res and res.status_code == 200:
        soup = BeautifulSoup(res.text, "xml")
        for s in soup.find_all("sitemap"):
            loc = s.find("loc")
            if loc and loc.text:
                sitemap_datoteke.append(loc.text.strip())

    # Fallback ako index zakataji
    if not sitemap_datoteke:
        sitemap_datoteke = [
            "https://cedeterijanova.hr/xml/sitemap_3_1.xml",
            "https://cedeterijanova.hr/xml/sitemap_3_1_images.xml",
            "https://cedeterijanova.hr/xml/sitemap_3_2_images.xml",
            "https://cedeterijanova.hr/xml/sitemap_3_3_images.xml"
        ]

    proizvodi = {} # ključ: link, vrijednost: {slika, naslov_iz_sitemapa}

    for sm in sitemap_datoteke:
        res_sm = sigurni_get(sm)
        if not res_sm or res_sm.status_code != 200:
            continue
        soup_sm = BeautifulSoup(res_sm.text, "xml")
        
        for url_tag in soup_sm.find_all("url"):
            loc = url_tag.find("loc")
            if not loc or not loc.text:
                continue
            puni_link = loc.text.strip().split("?")[0].rstrip("/")
            
            if puni_link == SITE_URL or any(x in puni_link for x in ["/kosarica", "/korpa", "/prijava", "/registracija", "/checkout", "/cart", "/kontakt", "/o-nama"]):
                continue

            # Provjera ima li sliku i naslov unutar image sitemapa
            img_url = ""
            img_title = ""
            image_tag = url_tag.find("image:image")
            if image_tag:
                il = image_tag.find("image:loc")
                it = image_tag.find("image:title")
                if il and il.text:
                    img_url = il.text.strip()
                if it and it.text:
                    img_title = html.unescape(it.text.strip())

            if puni_link not in proizvodi:
                proizvodi[puni_link] = {"slika": img_url, "naslov": img_title}
            else:
                if img_url and not proizvodi[puni_link]["slika"]:
                    proizvodi[puni_link]["slika"] = img_url
                if img_title and not proizvodi[puni_link]["naslov"]:
                    proizvodi[puni_link]["naslov"] = img_title

    return proizvodi

def parsiraj_proizvod(item):
    link, meta = item
    res = sigurni_get(link, timeout=20)
    if not res or res.status_code != 200:
        return None

    soup = BeautifulSoup(res.text, "lxml")

    # Provjera je li artikal LP ili singl (preskačemo CD-ove i Blu-Ray izdanja ako nas zanimaju samo ploče)
    # Možeš prilagoditi uvjet ako želiš i CD-ove, ali prema prethodnom kodu tražimo LP/vinile.
    naslov = meta["naslov"]
    if not naslov:
        performer_el = soup.select_one(".product-performer-wrapper")
        title_el = soup.select_one("h1.product-title, h1, .product-info h3")
        izvodjac = html.unescape(performer_el.get_text(" ", strip=True)).strip() if performer_el else ""
        album = html.unescape(title_el.get_text(" ", strip=True)).strip() if title_el else ""
        if izvodjac and album and izvodjac.lower() not in album.lower():
            naslov = f"{izvodjac} - {album}"
        else:
            naslov = album or izvodjac
            if not naslov and soup.title:
                naslov = soup.title.get_text(strip=True)

    if not naslov:
        return None

    # Filtriramo da uzmemo samo LP i singl ploče (ako u nazivu ili linku piše CD, Blu-ray i sl., preskačemo)
    link_lower = link.lower()
    naslov_lower = naslov.lower()
    if "-cd" in link_lower or " cd" in naslov_lower or "blu-ray" in link_lower or "dvd" in link_lower:
        return None

    # Provjera je li proizvod dostupan za kupovinu
    forma_kosarica = soup.select_one('form.item-cart, form[action*="add_to_cart"]')
    if not forma_kosarica:
        return None
    if forma_kosarica.select_one("button[disabled], input[disabled]"):
        return None

    # Cijena
    price_el = soup.select_one("span.price-current, .product-card-current-price, .price")
    cijena = "0.00"
    if price_el:
        tekst = price_el.get_text(" ", strip=True).replace(".", "").replace(",", ".")
        m = re.search(r"(\d+(?:\.\d{1,2})?)", tekst)
        if m:
            try:
                val = float(m.group(1))
                if val > 0:
                    cijena = f"{val:.2f}"
            except Exception:
                pass

    if cijena == "0.00":
        return None

    slika_url = meta["slika"]
    if not slika_url:
        img_el = soup.select_one("div.product-image img, .gallery img, img")
        if img_el:
            for attr in ["src", "data-src", "data-lazy-src"]:
                raw_s = img_el.get(attr, "")
                if raw_s and not any(z in raw_s.lower() for z in ["placeholder", "default", "svg"]):
                    slika_url = urljoin(SITE_URL, raw_s)
                    break

    tip_artikla = 'Singl 7"' if ('7"' in naslov_lower or "singl" in naslov_lower) else "LP"

    return (link, [naslov, cijena, link, slika_url, "Novo", "Novo", tip_artikla])

def scrape_cedeterija():
    t_start = time.time()
    sve_ploce = {}

    if os.path.exists(CSV_FILENAME):
        with open(CSV_FILENAME, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) >= 7:
                    norm_k = row[2].strip().split("?")[0].rstrip("/")
                    row[2] = norm_k
                    sve_ploce[norm_k] = row
        print(f"📦 Učitano {len(sve_ploce)} postojećih zapisa iz {CSV_FILENAME}.", flush=True)

    print("🗺️ Čitam XML sitemape i sakupljam sve proizvode...", flush=True)
    proizvodi_dict = dohvati_podatke_iz_sitemapa()
    print(f"✅ Iz sitemapa učitano {len(proizvodi_dict)} ukupnih stavki.", flush=True)

    if not proizvodi_dict:
        print("❌ Nema pronađenih stavki u sitemapu. Prekidam.", flush=True)
        return

    zadaci = list(proizvodi_dict.items())
    novi_zisi = {}
    uspjesno = 0

    print(f"⚡ Provjeravam dostupnost i cijene za {len(zadaci)} artikala s {BROJ_NITI} niti...", flush=True)
    with ThreadPoolExecutor(max_workers=BROJ_NITI) as executor:
        rezultati = list(executor.map(parsiraj_proizvod, zadaci))
        for res in rezultati:
            if res:
                link, red = res
                novi_zisi[link] = red
                sve_ploce[link] = red
                uspjesno += 1

    print(f"✅ Obrada završena! Pronađeno dostupnih LP ploča: {uspjesno}", flush=True)

    aktivni_linkovi = set(novi_zisi.keys())
    if len(aktivni_linkovi) > 10:
        pocetni_broj = len(sve_ploce)
        sve_ploce = {k: v for k, v in sve_ploce.items() if k in aktivni_linkovi}
        obrisano = pocetni_broj - len(sve_ploce)
        print(f"🧹 Uklonjeno {obrisano} rasprodanih artikala iz baze.", flush=True)

    if sve_ploce:
        with open(CSV_FILENAME, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Naslov", "Cijena", "URL_Proizvoda", "URL_Slike", "Stanje_Medija", "Stanje_Omota", "Tip_Artikla"])
            writer.writerows(sve_ploce.values())
        ukupno_slika = sum(1 for v in sve_ploce.values() if len(v) >= 4 and v[3])
        print(f"🎉 GOTOVO za {time.time() - t_start:.1f}s! U bazi je {len(sve_ploce)} ploča (Sa slikom: {ukupno_slika}).", flush=True)

if __name__ == "__main__":
    scrape_cedeterija()
