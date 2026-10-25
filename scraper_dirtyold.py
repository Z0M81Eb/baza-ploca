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

SITE_URL = "https://www.dirtyoldempire.com"
BASE_VINYL_URL = "https://www.dirtyoldempire.com/kategorija-proizvoda/vinyl-2/vinil/"
CSV_FILENAME = "dirtyold_ploce.csv"
BROJ_NITI = 4  # Samo 7-8 stranica ukupno, 4 niti su sasvim dovoljne i sigurne

_thread_local = threading.local()


def kreiraj_sesiju():
    s = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "hr-HR,hr;q=0.9,en-US;q=0.8,en;q=0.7",
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


def sigurni_get_utf8(url, timeout=45):
    for pokusaj in range(1, 5):
        try:
            s = dohvati_sesiju()
            r = s.get(url, timeout=timeout)
            if r.status_code == 200 and len(r.content) > 5000:
                # Forsiramo UTF-8 dekodiranje kako bismo izbjegli 'Ä\x8d' i 'â\x80\x93' artefakte
                return r.content.decode("utf-8", errors="replace")
            time.sleep(1.5 * pokusaj)
            _thread_local.session = kreiraj_sesiju()
        except Exception as e:
            print(f"   ⚠️ Greška spajanja ({pokusaj}/4) na {url}: {e}", flush=True)
            time.sleep(1.5 * pokusaj)
            _thread_local.session = kreiraj_sesiju()
    return None


def normaliziraj_link(href):
    if not href:
        return ""
    puni = urljoin(SITE_URL, html.unescape(str(href)).strip())
    return puni.split("?")[0].rstrip("/") + "/"


def izvuci_najbolju_sliku(img_el):
    if not img_el:
        return ""

    # Tražimo 600x600 ili originalnu sliku iz srcseta, a ako nema uzimamo src
    srcset = html.unescape(img_el.get("srcset", "")).strip()
    if srcset:
        kandidati = []
        for dio in srcset.split(","):
            dijelovi = dio.strip().split(" ")
            if dijelovi:
                url_s = urljoin(SITE_URL, dijelovi[0].strip())
                sirina = 0
                if len(dijelovi) > 1 and dijelovi[1].endswith("w") and dijelovi[1][:-1].isdigit():
                    sirina = int(dijelovi[1][:-1])
                if "placeholder" not in url_s.lower():
                    kandidati.append((sirina, url_s))
        if kandidati:
            # Preferiramo 600w (ili najveću dostupnu)
            for sirina, url_s in sorted(kandidati, key=lambda x: x[0]):
                if sirina >= 600:
                    return url_s.replace(" ", "%20")
            return kandidati[-1][1].replace(" ", "%20")

    for attr in ["data-large_image", "data-src", "src"]:
        val = html.unescape(img_el.get(attr, "")).strip()
        if val and "placeholder" not in val.lower() and not val.startswith("data:image"):
            return urljoin(SITE_URL, val).replace(" ", "%20")

    return ""


def izvuci_cijenu(kartica):
    price_span = kartica.select_one("span.price")
    if not price_span:
        return "0.00"

    # Ako je proizvod na akciji, WooCommerce stavlja novu cijenu unutar <ins>, a staru u <del>
    ins_el = price_span.select_one("ins .woocommerce-Price-amount, ins bdi")
    target_el = ins_el if ins_el else price_span.select_one(".woocommerce-Price-amount, bdi")
    if not target_el:
        target_el = price_span

    tekst = target_el.get_text(" ", strip=True)
    tekst = tekst.replace(".", "").replace(",", ".") if "," in tekst and "." in tekst else tekst.replace(",", ".")
    m = re.search(r"(\d+(?:\.\d{1,2})?)", tekst)
    if m:
        try:
            val = float(m.group(1))
            if val > 0:
                return f"{val:.2f}"
        except Exception:
            pass
    return "0.00"


def odredi_tip_artikla(klase_kartice, naslov):
    klase_str = " ".join(klase_kartice).lower()
    naslov_low = naslov.lower()

    # Provjera klasa kategorija na <li class="product ...">
    if "product_cat-single" in klase_str or "product_cat-7-box-set" in klase_str:
        return 'Singl 7"'

    if any(oznaka in naslov_low for oznaka in [' 7"', ' 7″', ' 7\'\'', ' 7′']):
        return 'Singl 7"'

    return "LP"


def izvuci_max_stranicu(soup):
    max_str = 1
    for a in soup.select(".woocommerce-pagination a.page-numbers, ul.page-numbers a[href]"):
        tekst = a.get_text(strip=True)
        if tekst.isdigit():
            max_str = max(max_str, int(tekst))
        m = re.search(r"/page/(\d+)/?", a.get("href", ""))
        if m:
            max_str = max(max_str, int(m.group(1)))
    return max_str


def parsiraj_stranicu(html_tekst):
    soup = BeautifulSoup(html_tekst, "lxml")
    dostupne_stavke = []

    kartice = soup.select("li.product")
    vidjeni_na_stranici = set()

    for k in kartice:
        klase = k.get("class", [])
        klase_low = [c.lower() for c in klase]

        # Sigurnosna provjera da ne uđe pribor ili slipmat
        if any(z in klase_low for z in ["product_cat-pribor-accessories", "product_cat-slipmat"]):
            continue

        # Provjera je li na zalihi (klasa 'instock' i gumb 'add_to_cart_button')
        if "outofstock" in klase_low or "instock" not in klase_low:
            continue
        if not k.select_one("a.add_to_cart_button"):
            continue

        a_link = k.select_one("a.woocommerce-LoopProduct-link, a[href*='/proizvod/']")
        if not a_link:
            continue

        link = normaliziraj_link(a_link.get("href", ""))
        if not link or link in vidjeni_na_stranici:
            continue

        title_el = k.select_one("h2.woocommerce-loop-product__title, .woocommerce-loop-product__title, h2")
        if not title_el:
            continue

        naslov = re.sub(r"\s+", " ", html.unescape(title_el.get_text(" ", strip=True))).strip()
        if not naslov:
            continue

        cijena = izvuci_cijenu(k)
        if cijena == "0.00":
            continue

        img_el = k.select_one("img")
        slika_url = izvuci_najbolju_sliku(img_el)

        tip_artikla = odredi_tip_artikla(klase_low, naslov)

        # Dirty Old Shop prodaje isključivo nove ploče (osim ako u naslovu izričito piše used/second hand)
        stanje = "Rabljeni" if any(r in naslov.lower() for r in ["second hand", "used", "rabljeno"]) else "Novo"

        vidjeni_na_stranici.add(link)
        dostupne_stavke.append((link, [naslov, cijena, link, slika_url, stanje, stanje, tip_artikla]))

    max_str = izvuci_max_stranicu(soup)
    return dostupne_stavke, len(kartice), max_str


def sastavi_url_stranice(page):
    if page <= 1:
        return BASE_VINYL_URL
    return f"{BASE_VINYL_URL.rstrip('/')}/page/{page}/"


def obradi_stranicu(page):
    url = sastavi_url_stranice(page)
    t0 = time.time()
    html_tekst = sigurni_get_utf8(url)
    if not html_tekst:
        return page, False, [], 0, 1, time.time() - t0
    stavke, br_kartica, max_str = parsiraj_stranicu(html_tekst)
    return page, True, stavke, br_kartica, max_str, time.time() - t0


def scrape_dirtyold():
    t_start = time.time()
    sve_ploce = {}
    aktivni_linkovi = set()

    if os.path.exists(CSV_FILENAME):
        with open(CSV_FILENAME, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) >= 7:
                    norm_k = normaliziraj_link(row[2])
                    row[2] = norm_k
                    sve_ploce[norm_k] = row
        print(f"📦 Učitano {len(sve_ploce)} postojećih zapisa iz {CSV_FILENAME}.", flush=True)

    print(f"🔎 Dohvaćam 1. stranicu Dirty Old Shop vinil kataloga ({BASE_VINYL_URL})...", flush=True)
    _, ok1, stavke1, br_kartica1, max_pages, trajanje1 = obradi_stranicu(1)

    if not ok1 or br_kartica1 == 0:
        print("❌ Neuspješno dohvaćanje 1. stranice. Prekidam rad.", flush=True)
        return

    for link, red in stavke1:
        sve_ploce[link] = red
        aktivni_linkovi.add(link)

    print(
        f"✅ Stranica 1/{max_pages} obrađena za {trajanje1:.1f}s: "
        f"{br_kartica1} kartica ({len(stavke1)} dostupnih na zalihi).",
        flush=True
    )

    if max_pages > 1:
        preostale = list(range(2, max_pages + 1))
        with ThreadPoolExecutor(max_workers=BROJ_NITI) as executor:
            for i in range(0, len(preostale), BROJ_NITI):
                blok = preostale[i:i + BROJ_NITI]
                rezultati = list(executor.map(obradi_stranicu, blok))

                for page, ok, stavke, br_kartica, _, trajanje in rezultati:
                    if not ok:
                        print(f"   ❌ Stranica {page}/{max_pages} nije uspjela!", flush=True)
                        continue

                    novih = 0
                    for link, red in stavke:
                        if link not in aktivni_linkovi:
                            novih += 1
                        sve_ploce[link] = red
                        aktivni_linkovi.add(link)

                    print(
                        f"   ⚡ Stranica {page}/{max_pages} ({trajanje:.1f}s): "
                        f"{br_kartica} kartica -> +{novih} dostupnih | Ukupno aktivnih: {len(aktivni_linkovi)}",
                        flush=True
                    )

    # Sinkronizacija: uklanjamo rasprodane artikle ako je katalog uredno pročitan
    if len(aktivni_linkovi) > 1000:
        pocetni_broj = len(sve_ploce)
        sve_ploce = {k: v for k, v in sve_ploce.items() if k in aktivni_linkovi}
        obrisano = pocetni_broj - len(sve_ploce)
        print(f"\n🧹 Sinkronizacija završena! Uklonjeno {obrisano} rasprodanih artikala.", flush=True)
    else:
        print(
            f"\n⚠️ Oprez: Pronađeno samo {len(aktivni_linkovi)} artikala. "
            f"Preskačem brisanje starih zapisa radi sigurnosti.",
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
            f"U bazi je {len(sve_ploce)} dostupnih Dirty Old Shop vinila (Sa slikom: {ukupno_slika}).",
            flush=True
        )


if __name__ == "__main__":
    scrape_dirtyold()
