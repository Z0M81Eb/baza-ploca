import cloudscraper
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse, parse_qs
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

# Fallback lista vinilnih podkategorija (ako izbornik na naslovnici ikada zakaže)
ZADANE_VINIL_KATEGORIJE = [
    ("LP", "https://freebird.hr/index.php?route=product/category&path=225_223", "LP"),
    ('7"', "https://freebird.hr/index.php?route=product/category&path=225_233", 'Singl 7"'),
    ('12" SINGLE', "https://freebird.hr/index.php?route=product/category&path=225_301", "LP"),
    ('10"', "https://freebird.hr/index.php?route=product/category&path=225_235", "LP"),
    ("MINI LP", "https://freebird.hr/index.php?route=product/category&path=225_247", "LP"),
    ('6"', "https://freebird.hr/index.php?route=product/category&path=225_232", 'Singl 7"'),
    ('12" VINYL', "https://freebird.hr/index.php?route=product/category&path=225_3209", "LP"),
    ('5"', "https://freebird.hr/index.php?route=product/category&path=225_231", 'Singl 7"'),
    ('12", MiniAlbum', "https://freebird.hr/index.php?route=product/category&path=225_3217", "LP"),
    ('12"', "https://freebird.hr/index.php?route=product/category&path=225_236", "LP"),
    ('10\'"', "https://freebird.hr/index.php?route=product/category&path=225_3212", "LP"),
    ("10''", "https://freebird.hr/index.php?route=product/category&path=225_339", "LP"),
    ('12" SINGLE + 7"', "https://freebird.hr/index.php?route=product/category&path=225_3216", "LP"),
    ("EP", "https://freebird.hr/index.php?route=product/category&path=225_315", 'Singl 7"'),
    ('LP & 12" SINGLE', "https://freebird.hr/index.php?route=product/category&path=225_328", "LP"),
    ("LP + 12 MAXI SINGLE", "https://freebird.hr/index.php?route=product/category&path=225_3203", "LP"),
    ("LP + EP", "https://freebird.hr/index.php?route=product/category&path=225_333", "LP"),
    ('8"', "https://freebird.hr/index.php?route=product/category&path=225_234", 'Singl 7"'),
]

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
            print(f"   ⚠️ Greška spajanja ({pokusaj}/4) na {url}: {e}", flush=True)
            time.sleep(1.5 * pokusaj)
            _thread_local.session = kreiraj_sesiju()
    return None


def odredi_tip_artikla_iz_naziva_kategorije(naziv_kat):
    n_up = naziv_kat.upper()
    if any(oznaka in n_up for oznaka in ['7"', "7'", '6"', '5"', '8"', "EP", "FLEXI"]):
        if "LP" not in n_up and '12"' not in n_up:
            return 'Singl 7"'
    return "LP"


def otkrij_vinil_kategorije():
    print("🔎 Automatski otkrivam aktivne vinil kategorije s naslovnice...", flush=True)
    html_tekst = sigurni_get(SITE_URL)
    if not html_tekst:
        print("⚠️ Neuspješan dohvat naslovnice, koristim zadane vinil kategorije.", flush=True)
        return ZADANE_VINIL_KATEGORIJE

    soup = BeautifulSoup(html_tekst, "lxml")
    otkrivene = []
    vidjeni_pathovi = set()

    for a in soup.select('a[href*="route=product/category"]'):
        href = html.unescape(a.get("href", "")).strip()
        tekst = " ".join(a.get_text(" ", strip=True).split())
        if not href or not tekst:
            continue

        # Ciljamo isključivo podkategorije unutar glavne VINYL grane (path=225_...)
        m_path = re.search(r"path=(225_\d+)", href)
        if not m_path:
            continue

        path_val = m_path.group(1)
        if path_val in vidjeni_pathovi:
            continue

        # Provjera završava li naziv kategorije brojem artikala (npr. "LP 9475" ili "10' 0")
        m_broj = re.search(r"\s+(\d+)$", tekst)
        if m_broj:
            broj_artikala = int(m_broj.group(1))
            cisti_naziv = tekst[:m_broj.start()].strip()
            if broj_artikala == 0:
                # Preskačemo prazne kategorije s 0 artikala
                continue
        else:
            cisti_naziv = tekst

        vidjeni_pathovi.add(path_val)
        cisti_url = f"{SITE_URL}/index.php?route=product/category&path={path_val}"
        tip = odredi_tip_artikla_iz_naziva_kategorije(cisti_naziv)
        otkrivene.append((cisti_naziv, cisti_url, tip))

    if not otkrivene:
        print("⚠️ Nisu pronađene path=225_ kategorije u meniju, koristim zadane.", flush=True)
        return ZADANE_VINIL_KATEGORIJE

    print(f"✅ Pronađeno {len(otkrivene)} aktivnih vinil podkategorija (prazne kategorije s 0 artikala preskočene).", flush=True)
    return otkrivene


def normaliziraj_link_proizvoda(href):
    if not href:
        return ""
    cisto = html.unescape(str(href)).strip()
    puni = urljoin(SITE_URL, cisto)
    # Izvlačimo čisti kanonski OpenCart link: https://freebird.hr/index.php?route=product/product&product_id=XXXXX
    m_id = re.search(r"product_id=(\d+)", puni)
    if m_id:
        return f"{SITE_URL}/index.php?route=product/product&product_id={m_id.group(1)}"
    return puni.split("&limit=")[0].split("&page=")[0].rstrip("/")


def izvuci_najbolju_sliku(img_el):
    if not img_el:
        return ""

    # Prvo provjeravamo srcset kako bismo uzeli 2x (500x500h.webp) verziju umjesto male 250x250
    srcset = html.unescape(img_el.get("srcset", "")).strip()
    if srcset:
        dijelovi = [d.strip().split(" ")[0] for d in srcset.split(",") if d.strip()]
        if dijelovi:
            najveca = urljoin(SITE_URL, dijelovi[-1])
            if "placeholder" not in najveca.lower():
                return najveca.replace(" ", "%20")

    for attr in ["data-largeimg", "src", "data-src"]:
        val = html.unescape(img_el.get(attr, "")).strip()
        if val and "placeholder" not in val.lower() and not val.startswith("data:image"):
            puni = urljoin(SITE_URL, val)
            return puni.replace(" ", "%20")

    return ""


def izvuci_stanja_iz_opisa(opis_tekst):
    # Primjer: "Condition sleeve: VG+Condition records: VG+Edition: Diskos..."
    stanje_omota = "Rabljeni"
    stanje_medija = "Rabljeni"

    if not opis_tekst:
        return stanje_medija, stanje_omota

    m_sleeve = re.search(
        r"Condition\s+sleeve\s*:\s*([A-Za-z0-9+\-\s/]*?)(?=Condition\s+records|Edition|Genre|Style|Remark|$)",
        opis_tekst,
        flags=re.IGNORECASE
    )
    if m_sleeve:
        val_s = m_sleeve.group(1).strip(" .,-:")
        if val_s:
            stanje_omota = val_s
        elif "no original cover" in opis_tekst.lower():
            stanje_omota = "Bez omota"
        else:
            stanje_omota = "-"

    m_records = re.search(
        r"Condition\s+records\s*:\s*([A-Za-z0-9+\-\s/]*?)(?=Edition|Genre|Style|Remark|$)",
        opis_tekst,
        flags=re.IGNORECASE
    )
    if m_records:
        val_r = m_records.group(1).strip(" .,-:")
        if val_r:
            stanje_medija = val_r

    return stanje_medija, stanje_omota


def izvuci_cijenu(kartica):
    # Prvo gledamo akcijsku cijenu (.price-new), a ako nema akcije onda običnu (.price-normal ili .price)
    for sel in ["span.price-new", "span.price-normal", "div.price"]:
        el = kartica.select_one(sel)
        if el:
            tekst = el.get_text(" ", strip=True).split("Ex Tax")[0]
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


def izvuci_broj_stranica(soup):
    # Primjer: "Showing 1 to 100 of 9475 (95 Pages)"
    for el in soup.select(".pagination-results div, .results"):
        tekst = el.get_text(" ", strip=True)
        m = re.search(r"\((\d+)\s+Pages?\)", tekst, flags=re.IGNORECASE)
        if m:
            return int(m.group(1))

    max_str = 1
    for a in soup.select("ul.pagination a[href]"):
        m_p = re.search(r"[?&]page=(\d+)", a["href"])
        if m_p:
            br = int(m_p.group(1))
            if br > max_str:
                max_str = br
    return max_str


def parsiraj_stranicu_kataloga(html_tekst, zadani_tip):
    soup = BeautifulSoup(html_tekst, "lxml")
    stavke = []

    # Ciljamo glavni katalog (.main-products) kako bismo ignorirali bočne module (Outlet/Bestsellers)
    glavni_spremnik = soup.select_one("div.main-products, #content div.main-products")
    if glavni_spremnik:
        kartice = glavni_spremnik.select("div.product-thumb")
    else:
        # Fallback: uzimamo samo kartice koje nisu unutar bočnih modula (module-products-...)
        sve_kartice = soup.select("div.product-thumb")
        kartice = []
        for k in sve_kartice:
            q_btn = k.select_one("a.btn-quickview")
            tooltip_klasa = q_btn.get("data-tooltip-class", "") if q_btn else ""
            if "module-products-" not in tooltip_klasa:
                kartice.append(k)
        if not kartice:
            kartice = sve_kartice

    vidjeni_na_stranici = set()

    for k in kartice:
        # Provjera ima li aktivan gumb za košaricu
        btn_cart = k.select_one("a.btn-cart")
        if not btn_cart:
            continue
        klase_kartice = " ".join(k.get("class", [])).lower()
        if "out-of-stock" in klase_kartice:
            continue

        a_name = k.select_one("div.name a[href]") or k.select_one("a.product-img[href]")
        if not a_name:
            continue

        link = normaliziraj_link_proizvoda(a_name.get("href", ""))
        if not link or link in vidjeni_na_stranici:
            continue

        # Čišćenje višestrukih razmaka u nazivu (npr. "Adem Seferaj    - Ty Parti...")
        sirovi_naslov = html.unescape(a_name.get_text(" ", strip=True))
        naslov = re.sub(r"\s+", " ", sirovi_naslov).strip()
        if not naslov:
            continue

        cijena = izvuci_cijenu(k)
        if cijena == "0.00":
            continue

        img_el = k.select_one("a.product-img img") or k.select_one("img")
        slika_url = izvuci_najbolju_sliku(img_el)

        opis_el = k.select_one("div.description")
        opis_tekst = html.unescape(opis_el.get_text(" ", strip=True)) if opis_el else ""
        stanje_medija, stanje_omota = izvuci_stanja_iz_opisa(opis_tekst)

        # Ako u Remarku piše da je EP ili 7", prilagođavamo tip artikla
        tip_artikla = zadani_tip
        if re.search(r"\bRemark\s*:\s*EP\b", opis_tekst, flags=re.IGNORECASE):
            tip_artikla = 'Singl 7"'

        vidjeni_na_stranici.add(link)
        stavke.append((link, [naslov, cijena, link, slika_url, stanje_medija, stanje_omota, tip_artikla]))

    max_str = izvuci_broj_stranica(soup)
    return stavke, len(kartice), max_str


def sastavi_url(baza_url, page):
    url = f"{baza_url}&limit={LIMIT_PO_STRANICI}"
    if page > 1:
        url += f"&page={page}"
    return url


def obradi_stranicu_zadatka(args):
    baza_url, page, zadani_tip = args
    url = sastavi_url(baza_url, page)
    html_tekst = sigurni_get(url)
    if not html_tekst:
        return page, False, [], 0, 1
    stavke, br_kartica, max_str = parsiraj_stranicu_kataloga(html_tekst, zadani_tip)
    return page, True, stavke, br_kartica, max_str


def scrape_freebird():
    t_start = time.time()
    sve_ploce = {}
    aktivni_linkovi = set()

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

    kategorije = otkrij_vinil_kategorije()

    with ThreadPoolExecutor(max_workers=BROJ_NITI) as executor:
        for idx_kat, (naziv_kat, kat_url, tip_artikla) in enumerate(kategorije, 1):
            t_kat = time.time()
            print(f"\n📂 [{idx_kat}/{len(kategorije)}] Kategorija '{naziv_kat}' ({tip_artikla}) -> {kat_url}", flush=True)

            _, ok1, stavke1, br_kartica1, max_pages = obradi_stranicu_zadatka((kat_url, 1, tip_artikla))
            if not ok1 or br_kartica1 == 0:
                print(f"   ℹ️ Nema pronađenih artikala u kategoriji '{naziv_kat}'.", flush=True)
                continue

            novih_u_kat = 0
            for link, red in stavke1:
                if link not in aktivni_linkovi:
                    novih_u_kat += 1
                sve_ploce[link] = red
                aktivni_linkovi.add(link)

            print(
                f"   ✅ Stranica 1/{max_pages}: +{len(stavke1)} artikala | "
                f"Ukupno u bazi: {len(aktivni_linkovi)}",
                flush=True
            )

            if max_pages > 1:
                preostale_stranice = list(range(2, max_pages + 1))
                for i in range(0, len(preostale_stranice), BROJ_NITI):
                    blok = preostale_stranice[i:i + BROJ_NITI]
                    zadaci = [(kat_url, p, tip_artikla) for p in blok]
                    t_blok = time.time()

                    rezultati = list(executor.map(obradi_stranicu_zadatka, zadaci))
                    uspjesno_str = 0
                    novih_u_bloku = 0

                    for page, ok, stavke, br_kartica, _ in rezultati:
                        if not ok:
                            continue
                        uspjesno_str += 1
                        for link, red in stavke:
                            if link not in aktivni_linkovi:
                                novih_u_bloku += 1
                                novih_u_kat += 1
                            sve_ploce[link] = red
                            aktivni_linkovi.add(link)

                    print(
                        f"   ⚡ [{naziv_kat}] Stranice {blok[0]:02d}-{blok[-1]:02d}/{max_pages} "
                        f"({uspjesno_str}/{len(blok)} OK) za {time.time() - t_blok:.1f}s | "
                        f"+{novih_u_bloku} novih | Ukupno: {len(aktivni_linkovi)}",
                        flush=True
                    )

            print(f"   🏁 Kategorija '{naziv_kat}' gotova za {time.time() - t_kat:.1f}s (+{novih_u_kat} unikatnih vinila).", flush=True)

    # Sinkronizacija: ako je katalog uspješno pretražen, uklanjamo prodane artikle
    if len(aktivni_linkovi) > 1000:
        pocetni_broj = len(sve_ploce)
        sve_ploce = {k: v for k, v in sve_ploce.items() if k in aktivni_linkovi}
        obrisano = pocetni_broj - len(sve_ploce)
        print(f"\n🧹 Sinkronizacija završena! Uklonjeno {obrisano} prodanih artikala.", flush=True)
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
            f"U bazi je {len(sve_ploce)} dostupnih Freebird vinila (Sa slikom: {ukupno_slika}).",
            flush=True
        )


if __name__ == "__main__":
    scrape_freebird()
