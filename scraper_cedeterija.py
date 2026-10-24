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

try:
    from curl_cffi import requests as cffi_requests
    IMA_CURL_CFFI = True
except ImportError:
    IMA_CURL_CFFI = False

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SITE_URL = "https://cedeterijanova.hr"
BASE_CATEGORY_URL = "https://cedeterijanova.hr/glazba/glazba-lp"
CSV_FILENAME = "cedeterija_ploce.csv"
BROJ_NITI = 6

_thread_local = threading.local()
_globalna_radna_sesija = None
_sesija_lock = threading.Lock()


def kreiraj_cloudscraper_sesiju():
    s = cloudscraper.create_scraper(
        browser={
            "browser": "chrome",
            "platform": "windows",
            "desktop": True
        }
    )
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "hr-HR,hr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": BASE_CATEGORY_URL,
        "Connection": "keep-alive"
    })
    try:
        s.get(SITE_URL + "/robots.txt", timeout=15)
        s.get(SITE_URL + "/", timeout=20)
    except Exception:
        pass
    return s


def dohvati_sesiju():
    global _globalna_radna_sesija
    s = getattr(_thread_local, "session", None)
    if s is None:
        s = kreiraj_cloudscraper_sesiju()
        if _globalna_radna_sesija is not None:
            try:
                s.cookies.update(_globalna_radna_sesija.cookies)
            except Exception:
                pass
        _thread_local.session = s
    return s


def je_li_valjana_stranica_kataloga(tekst):
    if not tekst or len(tekst) < 12000:
        return False
    t_low = tekst.lower()
    if "security check" in t_low or "we're so sorry" in t_low:
        return False
    return True


def sastavi_url_stranice(page):
    # Bez zabranjenih parametara iz robots.txt (page_size, sort, _price)
    if page <= 1:
        return BASE_CATEGORY_URL
    return f"{BASE_CATEGORY_URL}?page_number={page}"


def sigurni_get_katalog(url, max_pokusaja=6, timeout=30):
    global _globalna_radna_sesija

    for pokusaj in range(1, max_pokusaja + 1):
        try:
            s = dohvati_sesiju()
            r = s.get(url, timeout=timeout)
            duljina = len(r.text) if r and r.text else 0

            if r.status_code == 200 and je_li_valjana_stranica_kataloga(r.text):
                with _sesija_lock:
                    _globalna_radna_sesija = s
                return r.text

            # Fallback preko curl_cffi ako cloudscraper dobije soft-200 blokadu (7.9 KB)
            if IMA_CURL_CFFI and pokusaj % 2 == 0:
                profil = "chrome120" if pokusaj % 4 == 0 else "safari15_5"
                r_cffi = cffi_requests.get(
                    url,
                    impersonate=profil,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "hr-HR,hr;q=0.9,en-US;q=0.8",
                        "Referer": BASE_CATEGORY_URL
                    },
                    timeout=timeout
                )
                if r_cffi.status_code == 200 and je_li_valjana_stranica_kataloga(r_cffi.text):
                    return r_cffi.text

            time.sleep(1.5 * pokusaj)
            _thread_local.session = kreiraj_cloudscraper_sesiju()
            if _globalna_radna_sesija is not None:
                try:
                    _thread_local.session.cookies.update(_globalna_radna_sesija.cookies)
                except Exception:
                    pass
        except Exception:
            time.sleep(1.5 * pokusaj)
            _thread_local.session = kreiraj_cloudscraper_sesiju()

    return None


def normaliziraj_link_proizvoda(href):
    if not href:
        return ""
    puni = urljoin(SITE_URL, html.unescape(str(href)).strip())
    return puni.split("?")[0].rstrip("/")


def ocisti_url_slike(raw_url):
    if not raw_url:
        return ""
    val = html.unescape(str(raw_url)).strip()
    val_lower = val.lower()
    if any(z in val_lower for z in ["data:image", "placeholder", "no-image", "default", ".svg", "logo"]):
        return ""
    puni_url = urljoin(SITE_URL, val).strip()
    if not puni_url.startswith("http"):
        return ""
    return puni_url.replace(" ", "%20")


def izvuci_cijenu_iz_kartice(kartica):
    # Čitamo isključivo specifičnu klasu cijene proizvoda unutar kartice
    for el in kartica.select("span.price-current, .product-card-current-price, .current-price"):
        tekst = el.get_text(" ", strip=True).replace(".", "").replace(",", ".")
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
    brojevi = [1]
    for el in soup.select("[data-page-number]"):
        br = str(el.get("data-page-number", "")).strip()
        if br.isdigit():
            brojevi.append(int(br))
    for a in soup.select('a[href*="page_number="]'):
        m = re.search(r"page_number=(\d+)", a.get("href", ""))
        if m:
            brojevi.append(int(m.group(1)))
    return max(brojevi)


def parsiraj_cedeterija_stranicu(html_tekst, postojece_slike):
    soup = BeautifulSoup(html_tekst, "lxml")
    dostupne_stavke = []
    svi_linkovi_stranice = []

    kartice = soup.select("div.product-display-grid")
    if not kartice:
        kartice = soup.select("div[data-product-code], div.product-card, li.product")

    vidjeni_na_stranici = set()

    for k in kartice:
        a_img = k.select_one("a.product-display-grid-image") or k.select_one("a[href]")
        if not a_img or not a_img.get("href"):
            continue

        link = normaliziraj_link_proizvoda(a_img["href"])
        if not link or link == SITE_URL or link in vidjeni_na_stranici:
            continue

        vidjeni_na_stranici.add(link)
        svi_linkovi_stranice.append(link)

        # 1. Provjera je li proizvod dostupan za kupovinu
        forma_kosarica = k.select_one('form.item-cart, form[action*="add_to_cart"]')
        if not forma_kosarica:
            continue
        if forma_kosarica.select_one("button[disabled], input[disabled], .disabled"):
            continue

        # 2. Slika i naslov
        img_el = k.select_one("img")
        slika_iz_kartice = ""
        naslov_iz_slike = ""

        if img_el:
            for attr in ["src", "data-src", "data-lazy-src"]:
                slika_iz_kartice = ocisti_url_slike(img_el.get(attr, ""))
                if slika_iz_kartice:
                    break
            naslov_iz_slike = html.unescape(img_el.get("title") or img_el.get("alt") or "").strip()

        # Ako već imamo provjerenu sliku visoke rezolucije iz sitemapa u CSV-u, dajemo joj prednost
        slika_url = postojece_slike.get(link) or slika_iz_kartice

        performer_el = k.select_one(".product-performer-wrapper, .artist")
        title_el = k.select_one(".product-info h3, h3, .product-title")
        izvodjac = html.unescape(performer_el.get_text(" ", strip=True)).strip() if performer_el else ""
        album = html.unescape(title_el.get_text(" ", strip=True)).strip() if title_el else ""

        if izvodjac and album and izvodjac.lower() not in album.lower():
            naslov = f"{izvodjac} - {album}"
        else:
            naslov = naslov_iz_slike or album or izvodjac

        if not naslov:
            continue

        # 3. Točna cijena
        cijena = izvuci_cijenu_iz_kartice(k)
        if cijena == "0.00":
            continue

        naslov_low = naslov.lower()
        link_low = link.lower()
        tip_artikla = 'Singl 7"' if ('7"' in naslov_low or "singl" in naslov_low or "-singl" in link_low) else "LP"

        dostupne_stavke.append((link, [naslov, cijena, link, slika_url, "Novo", "Novo", tip_artikla]))

    max_str = izvuci_max_stranicu(soup)
    return dostupne_stavke, svi_linkovi_stranice, len(kartice), max_str


def dohvati_i_obradi_stranicu(args):
    page, postojece_slike = args
    url = sastavi_url_stranice(page)
    html_tekst = sigurni_get_katalog(url)
    if not html_tekst:
        return page, False, [], [], 0, 1

    dostupne_stavke, svi_linkovi, br_kartica, max_str = parsiraj_cedeterija_stranicu(html_tekst, postojece_slike)
    return page, True, dostupne_stavke, svi_linkovi, br_kartica, max_str


def scrape_cedeterija():
    t_start = time.time()
    sve_ploce = {}
    postojece_slike = {}

    if os.path.exists(CSV_FILENAME):
        with open(CSV_FILENAME, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) >= 7:
                    norm_k = normaliziraj_link_proizvoda(row[2])
                    row[2] = norm_k
                    sve_ploce[norm_k] = row
                    if row[3] and row[3].startswith("http"):
                        postojece_slike[norm_k] = row[3].strip()
        print(f"📦 Učitano {len(sve_ploce)} postojećih zapisa iz {CSV_FILENAME}.", flush=True)

    print("📄 Dohvaćam 1. stranicu Cedeterija LP kataloga (čista paginacija)...", flush=True)
    _, ok1, stavke1, svi_linkovi1, br_kartica1, max_pages = dohvati_i_obradi_stranicu((1, postojece_slike))

    if not ok1 or br_kartica1 == 0:
        print("❌ Neuspješno dohvaćanje 1. stranice. Prekidam kako ne bih obrisao postojeću bazu.", flush=True)
        return

    svi_vidjeni_linkovi_kataloga = set(svi_linkovi1)
    dostupni_vidjeni_linkovi = set()

    for link, red in stavke1:
        sve_ploce[link] = red
        dostupni_vidjeni_linkovi.add(link)

    print(
        f"✅ Stranica 1 obrađena: {br_kartica1} kartica ({len(stavke1)} dostupnih LP-a) | "
        f"Detektirano stranica u navigaciji: {max_pages}",
        flush=True
    )

    trenutna_stranica = 2
    kraj_kataloga = False
    uzastopnih_praznih_blokova = 0

    with ThreadPoolExecutor(max_workers=BROJ_NITI) as executor:
        while not kraj_kataloga:
            blok_stranica = list(range(trenutna_stranica, trenutna_stranica + BROJ_NITI))
            zadaci = [(p, postojece_slike) for p in blok_stranica]
            t_blok = time.time()

            rezultati = list(executor.map(dohvati_i_obradi_stranicu, zadaci))

            novih_dostupnih_u_bloku = 0
            novih_bilo_kakvih_linkova_u_bloku = 0
            uspjesno_stranica_u_bloku = 0

            for page, ok, stavke, svi_linkovi, br_kartica, novi_max in rezultati:
                if novi_max > max_pages:
                    max_pages = novi_max

                if not ok or br_kartica == 0:
                    continue

                uspjesno_stranica_u_bloku += 1

                for l_katalog in svi_linkovi:
                    if l_katalog not in svi_vidjeni_linkovi_kataloga:
                        svi_vidjeni_linkovi_kataloga.add(l_katalog)
                        novih_bilo_kakvih_linkova_u_bloku += 1

                for link, red in stavke:
                    if link not in dostupni_vidjeni_linkovi:
                        novih_dostupnih_u_bloku += 1
                    sve_ploce[link] = red
                    dostupni_vidjeni_linkovi.add(link)

            print(
                f"⚡ Stranice {blok_stranica[0]:03d}-{blok_stranica[-1]:03d} "
                f"({uspjesno_stranica_u_bloku}/{len(blok_stranica)} OK) za {time.time() - t_blok:.1f}s | "
                f"+{novih_dostupnih_u_bloku} dostupnih | Ukupno aktivnih: {len(dostupni_vidjeni_linkovi)}",
                flush=True
            )

            # Ako na cijelom bloku od 6 stranica nema nijednog novog artikla u katalogu, stigli smo do kraja
            if novih_bilo_kakvih_linkova_u_bloku == 0:
                uzastopnih_praznih_blokova += 1
                if uzastopnih_praznih_blokova >= 2 or blok_stranica[-1] >= max_pages:
                    kraj_kataloga = True
            else:
                uzastopnih_praznih_blokova = 0

            trenutna_stranica = blok_stranica[-1] + 1

    # Sigurnosna sinkronizacija: brišemo rasprodane samo ako je skripta uspješno prošla cijeli katalog
    if len(dostupni_vidjeni_linkovi) > 500:
        pocetni_broj = len(sve_ploce)
        sve_ploce = {k: v for k, v in sve_ploce.items() if k in dostupni_vidjeni_linkovi}
        obrisano = pocetni_broj - len(sve_ploce)
        print(f"\n🧹 Sinkronizacija završena! Uklonjeno {obrisano} rasprodanih artikala.", flush=True)
    else:
        print(
            f"\n⚠️ Oprez: Pronađeno samo {len(dostupni_vidjeni_linkovi)} artikala. "
            f"Preskačem brisanje starih zapisa radi sigurnosti baze.",
            flush=True
        )

    if sve_ploce:
        with open(CSV_FILENAME, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Naslov", "Cijena", "URL_Proizvoda", "URL_Slike", "Stanje_Medija", "Stanje_Omota", "Tip_Artikla"])
            writer.writerows(sve_ploce.values())
        ukupno_slika = sum(1 for v in sve_ploce.values() if len(v) >= 4 and v[3])
        print(
            f"🎉 GOTOVO za {time.time() - t_start:.1f}s! "
            f"U bazi je {len(sve_ploce)} dostupnih Cedeterija vinila (Sa slikom: {ukupno_slika}).",
            flush=True
        )


if __name__ == "__main__":
    scrape_cedeterija()
