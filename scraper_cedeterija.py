import requests as std_requests
from requests.adapters import HTTPAdapter
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

def dohvati_sesiju():
    s = getattr(_thread_local, "session", None)
    if s is None:
        s = std_requests.Session()
        s.verify = False
        adapter = HTTPAdapter(pool_connections=BROJ_NITI + 4, pool_maxsize=BROJ_NITI + 4)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "hr-HR,hr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": BASE_CATEGORY_URL,
            "Connection": "keep-alive"
        })
        _thread_local.session = s
    return s

def sigurni_get(url, timeout=25):
    for pokusaj in range(1, 4):
        try:
            s = dohvati_sesiju()
            r = s.get(url, timeout=timeout)
            if r.status_code in [200, 404]:
                return r
            time.sleep(1.5 * pokusaj)
        except Exception:
            time.sleep(1.5 * pokusaj)
    return None

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
    kartice = soup.select("div.product-display-grid.item")
    vidjeni_na_stranici = set()

    for k in kartice:
        # 1. Provjera je li proizvod dostupan za kupovinu (ima formu za košaricu)
        forma_kosarica = k.select_one('form.item-cart, form[action*="add_to_cart"]')
        if not forma_kosarica:
            continue

        gumb_disabled = forma_kosarica.select_one("button[disabled], input[disabled]")
        if gumb_disabled:
            continue

        # 2. Link i slika proizvoda
        a_img = k.select_one("a.product-display-grid-image")
        if not a_img or not a_img.get("href"):
            a_img = k.select_one("div.product-info a[href]")
        if not a_img or not a_img.get("href"):
            continue

        link = normaliziraj_link_proizvoda(a_img["href"])
        if not link or link in vidjeni_na_stranici:
            continue

        img_el = k.select_one("a.product-display-grid-image img") or k.select_one("img")
        slika_url = ""
        naslov_iz_slike = ""

        if img_el:
            for attr in ["src", "data-src", "data-lazy-src"]:
                slika_url = ocisti_url_slike(img_el.get(attr, ""))
                if slika_url:
                    break
            naslov_iz_slike = html.unescape(
                img_el.get("title") or img_el.get("alt") or ""
            ).strip()

        # 3. Naslov proizvoda (primarno iz title/alt slike, rezervno iz .product-info)
        naslov = naslov_iz_slike
        if not naslov:
            performer_el = k.select_one(".product-performer-wrapper")
            title_el = k.select_one(".product-info h3")
            izvodjac = html.unescape(performer_el.get_text(" ", strip=True)).strip() if performer_el else ""
            album = html.unescape(title_el.get_text(" ", strip=True)).strip() if title_el else ""
            if izvodjac and album and izvodjac.lower() not in album.lower():
                naslov = f"{izvodjac} - {album}"
            else:
                naslov = album or izvodjac

        if not naslov:
            continue

        # 4. Cijena
        price_el = k.select_one("span.price-current, .product-card-current-price")
        cijena = izvuci_cijenu(price_el)
        if cijena == "0.00":
            continue

        # 5. Tip artikla i stanje (sve ploče u Cedeteriji su nove)
        tip_el = k.select_one(".product-type")
        tip_tekst = tip_el.get_text(" ", strip=True).lower() if tip_el else ""
        if '7"' in naslov.lower() or '7"' in tip_tekst or "singl" in tip_tekst:
            tip_artikla = 'Singl 7"'
        else:
            tip_artikla = "LP"

        vidjeni_na_stranici.add(link)
        stavke.append((link, [naslov, cijena, link, slika_url, "Novo", "Novo", tip_artikla]))

    max_str = izvuci_max_stranicu(soup)
    return stavke, len(kartice), max_str

def dohvati_i_obradi_stranicu(page):
    if page == 1:
        url = BASE_CATEGORY_URL
    else:
        url = f"{BASE_CATEGORY_URL}?page_size=28&sort=3&page_number={page}&f=1"

    res = sigurni_get(url, timeout=25)
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

    print(
        f"✅ Stranica 1 obrađena: +{len(stavke1)} ploča (Otkriveno stranica u navigaciji: {max_pages})",
        flush=True
    )

    trenutna_stranica = 2
    kraj_kataloga = False
    neuspjesne_stranice = []

    with ThreadPoolExecutor(max_workers=BROJ_NITI) as executor:
        while not kraj_kataloga:
            # Ako paginacija prikazuje samo dio stranica (npr. 1..5), idemo u blokovima dok god ima novih artikala
            gornja_granica = max(max_pages, trenutna_stranica + BROJ_NITI - 1)
            blok = list(range(trenutna_stranica, min(gornja_granica, trenutna_stranica + BROJ_NITI * 2 - 1) + 1))
            if not blok:
                break

            t_blok = time.time()
            rezultati = list(executor.map(dohvati_i_obradi_stranicu, blok))

            novih_u_bloku = 0
            uspjesno_u_bloku = 0

            for page, ok, stavke, br_kartica, novi_max in rezultati:
                if novi_max > max_pages:
                    max_pages = novi_max

                if not ok:
                    neuspjesne_stranice.append(page)
                    continue

                if br_kartica == 0:
                    kraj_kataloga = True
                    continue

                uspjesno_u_bloku += 1
                novih_na_stranici = 0
                for link, red in stavke:
                    if link not in vidjeni_linkovi:
                        novih_na_stranici += 1
                        novih_u_bloku += 1
                    sve_ploce[link] = red
                    vidjeni_linkovi.add(link)

                # Ako stranica vrati isključivo već viđene ploče (vraćanje na zadnju stranicu), stajemo
                if novih_na_stranici == 0 and page >= max_pages:
                    kraj_kataloga = True

            print(
                f"⚡ Stranice {blok[0]:03d}-{blok[-1]:03d} ({uspjesno_u_bloku}/{len(blok)} OK) "
                f"za {time.time() - t_blok:.1f}s | +{novih_u_bloku} novih | Ukupno: {len(vidjeni_linkovi)}",
                flush=True
            )

            if novih_u_bloku == 0 or blok[-1] >= max_pages:
                if blok[-1] >= max_pages:
                    kraj_kataloga = True

            trenutna_stranica = blok[-1] + 1

        # Popravni krug ako je neka stranica imala privremeni timeout
        if neuspjesne_stranice:
            print(f"\n🔄 Ponavljam dohvat za {len(neuspjesne_stranice)} stranica...", flush=True)
            popravni_rez = list(executor.map(dohvati_i_obradi_stranicu, neuspjesne_stranice))
            preostale_greske = []
            for page, ok, stavke, br_kartica, _ in popravni_rez:
                if not ok:
                    preostale_greske.append(page)
                    continue
                for link, red in stavke:
                    sve_ploce[link] = red
                    vidjeni_linkovi.add(link)
            neuspjesne_stranice = preostale_greske

    if len(neuspjesne_stranice) == 0 and len(vidjeni_linkovi) > 20:
        pocetni_broj = len(sve_ploce)
        sve_ploce = {k: v for k, v in sve_ploce.items() if k in vidjeni_linkovi}
        obrisano = pocetni_broj - len(sve_ploce)
        print(f"\n🧹 Sinkronizacija završena! Uklonjeno {obrisano} rasprodanih artikala.", flush=True)
    elif neuspjesne_stranice:
        print(f"\n⚠️ Preskočeno {len(neuspjesne_stranice)} stranica — zadržavam stare zapise radi sigurnosti.", flush=True)

    if sve_ploce:
        with open(CSV_FILENAME, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Naslov", "Cijena", "URL_Proizvoda", "URL_Slike", "Stanje_Medija", "Stanje_Omota", "Tip_Artikla"])
            writer.writerows(sve_ploce.values())
        ukupno_slika = sum(1 for v in sve_ploce.values() if len(v) >= 4 and v[3])
        ukupno_sek = time.time() - t_start
        print(f"🎉 GOTOVO za {ukupno_sek:.1f}s! U bazi je {len(sve_ploce)} dostupnih Cedeterija vinila (Sa slikom: {ukupno_slika}).", flush=True)

if __name__ == "__main__":
    scrape_cedeterija()
