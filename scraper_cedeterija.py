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
BASE_CATEGORY_URL = "https://cedeterijanova.hr/glazba/glazba-lp"
CSV_FILENAME = "cedeterija_ploce.csv"
BROJ_NITI = 6

_thread_local = threading.local()

def kreiraj_i_inicijaliziraj_sesiju():
    # Kreiramo cloudscraper bez diranja .verify atributa koji je uzrokovao grešku
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
        print("🛡️ Rješavam Cloudflare provjeru preko naslovne stranice...", flush=True)
        r_home = s.get(SITE_URL, timeout=30)
        print(f"   🔎 Naslovna stranica HTTP status: {r_home.status_code} (Duljina: {len(r_home.text)})", flush=True)
        time.sleep(2)
    except Exception as e:
        print(f"   ⚠️ Greška pri inicijalizaciji naslovne stranice: {e}", flush=True)
        
    return s

def dohvati_sesiju():
    s = getattr(_thread_local, "session", None)
    if s is None:
        s = kreiraj_i_inicijaliziraj_sesiju()
        _thread_local.session = s
    return s

def sigurni_get(url, timeout=30):
    for pokusaj in range(1, 4):
        try:
            s = dohvati_sesiju()
            r = s.get(url, timeout=timeout)
            print(f"   🔎 [Pokušaj {pokusaj}] HTTP {r.status_code} | Veličina: {len(r.text)} znakova | URL: {url}", flush=True)
            if r.status_code == 200 and len(r.text) > 1000 and "security check" not in r.text.lower():
                return r
            time.sleep(2 * pokusaj)
            _thread_local.session = kreiraj_i_inicijaliziraj_sesiju()
        except Exception as e:
            print(f"   ⚠️ [Pokušaj {pokusaj}] Greška spajanja: {e}", flush=True)
            _thread_local.session = kreiraj_i_inicijaliziraj_sesiju()
            time.sleep(2 * pokusaj)
    return None

def sastavi_url_stranice(page):
    return f"{BASE_CATEGORY_URL}?page_size=28&sort=3&page_number={page}&f=1&min_price=5&max_price=445"

def normaliziraj_link_proizvoda(href):
    if not href:
        return ""
    puni = urljoin(SITE_URL, str(href).strip())
    return puni.split("?")[0].rstrip("/")

def ocisti_url_slike(raw_url):
    if not raw_url:
        return ""
    val = html.unescape(str(raw_url)).strip()
    val_lower = val.lower()
    if any(z in val_lower for z in ["data:image", "placeholder", "no-image", "default", ".svg"]):
        return ""
    puni_url = urljoin(SITE_URL, val)
    if not puni_url.startswith("http"):
        return ""
    return puni_url.replace(" ", "%20")

def izvuci_cijenu(price_el):
    if not price_el:
        return "0.00"
    tekst = price_el.get_text(" ", strip=True).replace(".", "").replace(",", ".")
    m = re.search(r"(\d+(?:\.\d{1,2})?)", tekst)
    if m:
        try:
            val = float(m.group(1))
            if val > 0:
                return f"{val:.2f}"
        except Exception:
            pass
    return "0.00"

def izvuci_max_stranicu(soup):
    brojevi = []
    for el in soup.select("[data-page-number]"):
        br = str(el.get("data-page-number", "")).strip()
        if br.isdigit():
            brojevi.append(int(br))
    return max(brojevi) if brojevi else 1

def parsiraj_cedeterija_stranicu(soup):
    stavke = []
    kartice = soup.select("div.product-display-grid")
    if not kartice:
        kartice = soup.select("div[data-product-code]")
    if not kartice:
        kartice = soup.select(".product-card, li.product, div.item")

    vidjeni_na_stranici = set()

    for k in kartice:
        a_img = k.select_one("a.product-display-grid-image") or k.select_one("a[href]")
        if not a_img or not a_img.get("href"):
            continue

        link = normaliziraj_link_proizvoda(a_img["href"])
        if not link or link in vidjeni_na_stranici:
            continue

        img_el = k.select_one("img")
        slika_url = ""
        naslov_iz_slike = ""

        if img_el:
            for attr in ["src", "data-src", "data-lazy-src"]:
                slika_url = ocisti_url_slike(img_el.get(attr, ""))
                if slika_url:
                    break
            naslov_iz_slike = html.unescape(img_el.get("title") or img_el.get("alt") or "").strip()

        naslov = naslov_iz_slike
        if not naslov:
            performer_el = k.select_one(".product-performer-wrapper")
            title_el = k.select_one(".product-info h3, h3")
            izvodjac = html.unescape(performer_el.get_text(" ", strip=True)).strip() if performer_el else ""
            album = html.unescape(title_el.get_text(" ", strip=True)).strip() if title_el else ""
            if izvodjac and album and izvodjac.lower() not in album.lower():
                naslov = f"{izvodjac} - {album}"
            else:
                naslov = album or izvodjac

        if not naslov:
            continue

        price_el = k.select_one("span.price-current, .product-card-current-price, .price")
        cijena = izvuci_cijenu(price_el)
        if cijena == "0.00":
            continue

        vidjeni_na_stranici.add(link)
        stavke.append((link, [naslov, cijena, link, slika_url, "Novo", "Novo", "LP"]))

    max_str = izvuci_max_stranicu(soup)
    return stavke, len(kartice), max_str

def dohvati_i_obradi_stranicu(page):
    url = sastavi_url_stranice(page)
    res = sigurni_get(url, timeout=30)
    if not res or res.status_code != 200:
        return page, False, [], 0, 1

    soup = BeautifulSoup(res.text, "lxml")
    stavke, ukupno_kartica, max_str = parsiraj_cedeterija_stranicu(soup)
    return page, True, stavke, ukupno_kartica, max_str

def scrape_cedeterija():
    t_start = time.time()
    sve_ploce = {}
    vidjeni_linkovi = set()

    if os.path.exists(CSV_FILENAME):
        with open(CSV_FILENAME, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) >= 7:
                    norm_k = normaliziraj_link_proizvoda(row[2])
                    row[2] = norm_k
                    sve_ploce[norm_k] = row
        print(f"📦 Učitano {len(sve_ploce)} postojećih zapisa iz {CSV_FILENAME}.", flush=True)

    print("📄 Dohvaćam 1. stranicu Cedeterija LP kataloga...", flush=True)
    _, ok1, stavke1, br_kartica1, max_pages = dohvati_i_obradi_stranicu(1)
    if not ok1 or br_kartica1 == 0:
        print("❌ Neuspješno dohvaćanje 1. stranice ili nema pronađenih kartica. Prekidam.", flush=True)
        return

    for link, red in stavke1:
        sve_ploce[link] = red
        vidjeni_linkovi.add(link)

    print(f"✅ Stranica 1 obrađena: +{len(stavke1)} ploča (Max stranica: {max_pages})", flush=True)

    trenutna_stranica = 2
    kraj_kataloga = False

    with ThreadPoolExecutor(max_workers=BROJ_NITI) as executor:
        while not kraj_kataloga:
            blok = list(range(trenutna_stranica, trenutna_stranica + BROJ_NITI))
            t_blok = time.time()
            rezultati = list(executor.map(dohvati_i_obradi_stranicu, blok))

            novih_u_bloku = 0
            uspjesno_u_bloku = 0

            for page, ok, stavke, br_kartica, novi_max in rezultati:
                if novi_max > max_pages:
                    max_pages = novi_max

                if not ok or br_kartica == 0:
                    continue

                uspjesno_u_bloku += 1
                novih_na_stranici = 0
                for link, red in stavke:
                    if link not in vidjeni_linkovi:
                        novih_na_stranici += 1
                        novih_u_bloku += 1
                    sve_ploce[link] = red
                    vidjeni_linkovi.add(link)

                if novih_na_stranici == 0 and page >= max_pages:
                    kraj_kataloga = True

            print(
                f"⚡ Stranice {blok[0]:03d}-{blok[-1]:03d} ({uspjesno_u_bloku}/{len(blok)} OK) "
                f"za {time.time() - t_blok:.1f}s | +{novih_u_bloku} novih | Ukupno: {len(vidjeni_linkovi)}",
                flush=True
            )

            if novih_u_bloku == 0 and blok[-1] >= max_pages:
                kraj_kataloga = True

            trenutna_stranica = blok[-1] + 1

    if len(vidjeni_linkovi) > 20:
        pocetni_broj = len(sve_ploce)
        sve_ploce = {k: v for k, v in sve_ploce.items() if k in vidjeni_linkovi}
        obrisano = pocetni_broj - len(sve_ploce)
        print(f"\n🧹 Sinkronizacija završena! Uklonjeno {obrisano} rasprodanih artikala.", flush=True)

    if sve_ploce:
        with open(CSV_FILENAME, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Naslov", "Cijena", "URL_Proizvoda", "URL_Slike", "Stanje_Medija", "Stanje_Omota", "Tip_Artikla"])
            writer.writerows(sve_ploce.values())
        ukupno_slika = sum(1 for v in sve_ploce.values() if len(v) >= 4 and v[3])
        print(f"🎉 GOTOVO za {time.time() - t_start:.1f}s! U bazi je {len(sve_ploce)} dostupnih Cedeterija vinila (Sa slikom: {ukupno_slika}).", flush=True)

if __name__ == "__main__":
    scrape_cedeterija()
