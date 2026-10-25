import cloudscraper
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import urllib3
import csv
import os
import re
import time
import html

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SITE_URL = "https://www.dirtyoldempire.com"
BASE_VINYL_URL = "https://www.dirtyoldempire.com/kategorija-proizvoda/vinyl-2/vinil/"
CSV_FILENAME = "dirtyold_ploce.csv"


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


def sigurni_get_utf8(sesija, url, max_pokusaja=6, timeout=60):
    for pokusaj in range(1, max_pokusaja + 1):
        try:
            r = sesija.get(url, timeout=timeout)

            # Ako je stranica izvan opsega paginacije, WooCommerce vraća 404
            if r.status_code == 404:
                return "STATUS_404", sesija

            if r.status_code == 200 and len(r.content) > 5000:
                tekst = r.content.decode("utf-8", errors="replace")
                if "li class=\"product" in tekst or "woocommerce-pagination" in tekst:
                    return tekst, sesija

            print(
                f"   ⚠️ Pokušaj {pokusaj}/{max_pokusaja} za {url} vratio HTTP {r.status_code} "
                f"(duljina: {len(r.content)}B). Čekam {2 * pokusaj}s...",
                flush=True
            )
            time.sleep(2 * pokusaj)
            sesija = kreiraj_sesiju()

        except Exception as e:
            print(f"   ⚠️ Mrežna greška ({pokusaj}/{max_pokusaja}) na {url}: {e}. Čekam {2 * pokusaj}s...", flush=True)
            time.sleep(2 * pokusaj)
            sesija = kreiraj_sesiju()

    return None, sesija


def normaliziraj_link(href):
    if not href:
        return ""
    puni = urljoin(SITE_URL, html.unescape(str(href)).strip())
    return puni.split("?")[0].rstrip("/") + "/"


def izvuci_najbolju_sliku(img_el):
    if not img_el:
        return ""

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

    if "product_cat-single" in klase_str or "product_cat-7-box-set" in klase_str:
        return 'Singl 7"'

    if any(oznaka in naslov_low for oznaka in [' 7"', ' 7″', " 7''", ' 7′']):
        return 'Singl 7"'

    return "LP"


def izvuci_max_stranicu(soup):
    # Prvo gledamo točan broj rezultata iz "Prikazujemo 1–1000 od XXXX rezultata"
    res_count = soup.select_one(".woocommerce-result-count")
    if res_count:
        tekst = res_count.get_text(" ", strip=True)
        m_ukupno = re.search(r"od\s+(\d+)\s+rezultat", tekst, flags=re.IGNORECASE)
        if m_ukupno:
            ukupno = int(m_ukupno.group(1))
            if ukupno > 0:
                return (ukupno + 999) // 1000

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

        if any(z in klase_low for z in ["product_cat-pribor-accessories", "product_cat-slipmat"]):
            continue

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
        stanje = "Rabljeni" if any(r in naslov.lower() for r in ["second hand", "used", "rabljeno"]) else "Novo"

        vidjeni_na_stranici.add(link)
        dostupne_stavke.append((link, [naslov, cijena, link, slika_url, stanje, stanje, tip_artikla]))

    max_str = izvuci_max_stranicu(soup)
    return dostupne_stavke, len(kartice), max_str


def sastavi_url_stranice(page):
    if page <= 1:
        return BASE_VINYL_URL
    return f"{BASE_VINYL_URL.rstrip('/')}/page/{page}/"


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

    sesija = kreiraj_sesiju()
    print(f"🔎 Dohvaćam 1. stranicu Dirty Old Shop vinil kataloga ({BASE_VINYL_URL})...", flush=True)

    t0 = time.time()
    html_1, sesija = sigurni_get_utf8(sesija, sastavi_url_stranice(1))
    if not html_1 or html_1 == "STATUS_404":
        print("❌ Neuspješno dohvaćanje 1. stranice. Prekidam rad.", flush=True)
        return

    stavke1, br_kartica1, max_pages = parsiraj_stranicu(html_1)
    if br_kartica1 == 0:
        print("❌ Na 1. stranici nisu pronađene kartice proizvoda. Prekidam rad.", flush=True)
        return

    for link, red in stavke1:
        sve_ploce[link] = red
        aktivni_linkovi.add(link)

    print(
        f"✅ Stranica 1/{max_pages} obrađena za {time.time() - t0:.1f}s: "
        f"{br_kartica1} kartica ({len(stavke1)} dostupnih na zalihi).",
        flush=True
    )

    neuspjele_stranice = []

    # Slijedno dohvaćanje preostalih stranica (bez paralelnog udara na bazu od po 1000 artikala)
    for page in range(2, max_pages + 1):
        time.sleep(1.2)
        t_p = time.time()
        url_p = sastavi_url_stranice(page)
        html_p, sesija = sigurni_get_utf8(sesija, url_p)

        if html_p == "STATUS_404":
            print(f"   ℹ️ Stranica {page}/{max_pages} vratila 404 (kraj kataloga).", flush=True)
            break

        if not html_p:
            print(f"   ❌ Stranica {page}/{max_pages} nije uspjela u prvom krugu!", flush=True)
            neuspjele_stranice.append(page)
            continue

        stavke, br_kartica, _ = parsiraj_stranicu(html_p)
        novih = 0
        for link, red in stavke:
            if link not in aktivni_linkovi:
                novih += 1
            sve_ploce[link] = red
            aktivni_linkovi.add(link)

        print(
            f"   ⚡ Stranica {page}/{max_pages} ({time.time() - t_p:.1f}s): "
            f"{br_kartica} kartica -> +{novih} dostupnih | Ukupno aktivnih: {len(aktivni_linkovi)}",
            flush=True
        )

    # Popravni krug za eventualno neuspjele stranice
    if neuspjele_stranice:
        print(f"\n🔄 Ponavljam dohvat za neuspjele stranice: {neuspjele_stranice} nakon 5s pauze...", flush=True)
        time.sleep(5)
        sesija = kreiraj_sesiju()
        jos_uvijek_neuspjele = []

        for page in neuspjele_stranice:
            t_p = time.time()
            html_p, sesija = sigurni_get_utf8(sesija, sastavi_url_stranice(page), max_pokusaja=6, timeout=60)
            if html_p and html_p != "STATUS_404":
                stavke, br_kartica, _ = parsiraj_stranicu(html_p)
                novih = 0
                for link, red in stavke:
                    if link not in aktivni_linkovi:
                        novih += 1
                    sve_ploce[link] = red
                    aktivni_linkovi.add(link)
                print(
                    f"   ✅ [Popravni] Stranica {page}/{max_pages} ({time.time() - t_p:.1f}s): "
                    f"{br_kartica} kartica -> +{novih} dostupnih | Ukupno aktivnih: {len(aktivni_linkovi)}",
                    flush=True
                )
            elif html_p == "STATUS_404":
                print(f"   ℹ️ [Popravni] Stranica {page} ne postoji (404).", flush=True)
            else:
                jos_uvijek_neuspjele.append(page)

        neuspjele_stranice = jos_uvijek_neuspjele

    # Sinkronizacija: brišemo rasprodane SAMO ako nijedna stranica nije pala!
    if not neuspjele_stranice and len(aktivni_linkovi) > 1000:
        pocetni_broj = len(sve_ploce)
        sve_ploce = {k: v for k, v in sve_ploce.items() if k in aktivni_linkovi}
        obrisano = pocetni_broj - len(sve_ploce)
        print(f"\n🧹 Sinkronizacija završena! Uklonjeno {obrisano} rasprodanih artikala.", flush=True)
    else:
        print(
            f"\n⚠️ Oprez: Neke stranice nisu učitane ({neuspjele_stranice}). "
            f"Zadržavam postojeće artikle u bazi bez brisanja.",
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
