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
from urllib.parse import urljoin, quote

try:
    from curl_cffi import requests as cffi_requests
    IMA_CURL_CFFI = True
except ImportError:
    IMA_CURL_CFFI = False

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SITE_URL = "https://cedeterijanova.hr"
CSV_FILENAME = "cedeterija_ploce.csv"
BROJ_NITI = 6

SITEMAP_INDEX_URL = "https://cedeterijanova.hr/xml/sitemap_3.xml"
POZNATI_SITEMAPI = [
    "https://cedeterijanova.hr/xml/sitemap_3_1.xml",
    "https://cedeterijanova.hr/xml/sitemap_3_1_images.xml",
    "https://cedeterijanova.hr/xml/sitemap_3_2_images.xml",
    "https://cedeterijanova.hr/xml/sitemap_3_3_images.xml",
]

_thread_local = threading.local()
_globalna_radna_sesija = None
_sesija_lock = threading.Lock()


def kreiraj_cloudscraper_sesiju():
    # Bez postavljanja .verify = False kako se ne bi rušio SSL check_hostname
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
        "Referer": SITE_URL + "/",
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


def je_li_blokiran_odgovor(tekst):
    if not tekst or len(tekst) < 8500:
        t_low = (tekst or "").lower()
        if "security check" in t_low or "we're so sorry" in t_low or len(t_low) < 1500:
            return True
    return False


def dohvati_xml_sitemap(url, max_pokusaja=8):
    global _globalna_radna_sesija

    for pokusaj in range(1, max_pokusaja + 1):
        try:
            s = dohvati_sesiju()
            r = s.get(url, timeout=45)
            duljina = len(r.text) if r and r.text else 0
            print(f"   🔎 [Sitemap pokušaj {pokusaj}] {url} -> HTTP {r.status_code} (Duljina: {duljina})", flush=True)

            if r.status_code == 200 and duljina > 10000 and ("<urlset" in r.text or "<sitemapindex" in r.text):
                with _sesija_lock:
                    _globalna_radna_sesija = s
                return r.text

            # Fallback preko curl_cffi ako cloudscraper zapne na WAF-u
            if IMA_CURL_CFFI and pokusaj % 2 == 0:
                profil = "chrome120" if pokusaj % 4 == 0 else "safari15_5"
                r_cffi = cffi_requests.get(
                    url,
                    impersonate=profil,
                    headers={
                        "Accept": "application/xml,text/xml,*/*;q=0.9",
                        "Accept-Language": "hr-HR,hr;q=0.9,en-US;q=0.8",
                        "Referer": SITE_URL + "/"
                    },
                    timeout=45
                )
                duljina_c = len(r_cffi.text) if r_cffi and r_cffi.text else 0
                print(f"   🔎 [curl_cffi {profil}] {url} -> HTTP {r_cffi.status_code} (Duljina: {duljina_c})", flush=True)
                if r_cffi.status_code == 200 and duljina_c > 10000 and ("<urlset" in r_cffi.text or "<sitemapindex" in r_cffi.text):
                    return r_cffi.text

            time.sleep(2 * pokusaj)
            _thread_local.session = kreiraj_cloudscraper_sesiju()
            if _globalna_radna_sesija is not None:
                try:
                    _thread_local.session.cookies.update(_globalna_radna_sesija.cookies)
                except Exception:
                    pass

        except Exception as e:
            print(f"   ⚠️ Greška pri dohvatu sitemapa {url}: {e}", flush=True)
            time.sleep(2 * pokusaj)
            _thread_local.session = kreiraj_cloudscraper_sesiju()

    return None


def sigurni_get_proizvod(url, timeout=25):
    for pokusaj in range(1, 4):
        try:
            s = dohvati_sesiju()
            r = s.get(url, timeout=timeout)
            if r.status_code == 200 and not je_li_blokiran_odgovor(r.text):
                return r.text
            time.sleep(1.5 * pokusaj)
            _thread_local.session = kreiraj_cloudscraper_sesiju()
        except Exception:
            time.sleep(1.5 * pokusaj)
            _thread_local.session = kreiraj_cloudscraper_sesiju()
    return None


def ocisti_cdata(tekst):
    if not tekst:
        return ""
    cisto = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", tekst, flags=re.DOTALL)
    return html.unescape(cisto).strip()


def normaliziraj_link_proizvoda(href):
    if not href:
        return ""
    cisto = ocisti_cdata(str(href))
    puni = urljoin(SITE_URL, cisto)
    return puni.split("?")[0].rstrip("/")


def ocisti_url_slike(raw_url):
    if not raw_url:
        return ""
    val = ocisti_cdata(str(raw_url))
    if not val:
        return ""
    val_lower = val.lower()
    if any(z in val_lower for z in ["data:image", "placeholder", "no-image", "default", ".svg", "logo"]):
        return ""
    puni_url = urljoin(SITE_URL, val).strip()
    if not puni_url.startswith("http"):
        return ""
    return puni_url.replace(" ", "%20")


def provjeri_format_iz_teksta(url, naslov):
    u_low = url.lower()
    n_up = (naslov or "").upper().strip()

    # 1. Eksplicitne oznake za NE-vinil formate (CD, Blu-Ray, DVD, MC, itd.)
    ne_vinil_url = ["-cd", "-cd2", "-cd3", "-dvd", "-blu-ray", "-bluray", "-sacd", "-mc", "-knjiga", "-majica"]
    if any(u_low.endswith(suf) or f"{suf}-" in u_low for suf in ne_vinil_url):
        if not any(v in u_low for v in ["-lp", "-vinyl"]):
            return "NE_VINIL"

    ne_vinil_naslov_kraj = (" CD", " CD2", " CD3", " DVD", " BLU-RAY", " BLURAY", " SACD", " MC", " CD+DVD")
    if n_up.endswith(ne_vinil_naslov_kraj) and "LP" not in n_up and "VINYL" not in n_up:
        return "NE_VINIL"

    # 2. Eksplicitne oznake za vinil (LP / Singl 7" / 12" Maxi)
    vinil_url = ["-lp", "-lp2", "-lp3", "-lp4", "-vinyl", "-12-maxi", "-singl", "-7-single"]
    if any(u_low.endswith(suf) or f"{suf}-" in u_low for suf in vinil_url):
        return "VINIL"

    vinil_uzorci = r"\b(LP|LP2|LP3|LP4|VINYL|12\"|12\"MAXI|7\"|SINGL|PICTURE DISC)\b"
    if re.search(vinil_uzorci, n_up):
        return "VINIL"

    return "NEPOZNATO"


def dohvati_podatke_iz_sitemapa():
    sitemap_lista = list(POZNATI_SITEMAPI)

    # Provjera glavnog sitemap indexa ako sadrži dodatne XML datoteke
    index_xml = dohvati_xml_sitemap(SITEMAP_INDEX_URL, max_pokusaja=4)
    if index_xml:
        pronadjeni = re.findall(r"<loc>\s*(?:<!\[CDATA\[)?\s*(https?://[^<\s\]]+\.xml)\s*(?:\]\]>)?\s*</loc>", index_xml, re.IGNORECASE)
        for sm_url in pronadjeni:
            sm_cist = sm_url.strip()
            if sm_cist not in sitemap_lista:
                sitemap_lista.append(sm_cist)

    proizvodi = {}
    neuspjesni_sitemapi = []

    # Prvi prolaz kroz sve sitemape
    for sm in sitemap_lista:
        print(f"🗺️ Dohvaćam sitemap: {sm}", flush=True)
        xml_tekst = dohvati_xml_sitemap(sm, max_pokusaja=6)
        if not xml_tekst:
            neuspjesni_sitemapi.append(sm)
            continue
        parsiraj_xml_u_mapu(xml_tekst, sm, proizvodi)

    # Drugi prolaz za sitemape koji su eventualno pali dok sesija još nije bila otključana
    if neuspjesni_sitemapi and _globalna_radna_sesija is not None:
        print(f"🔄 Ponavljam dohvat {len(neuspjesni_sitemapi)} sitemapa s otključanom sesijom...", flush=True)
        for sm in neuspjesni_sitemapi:
            xml_tekst = dohvati_xml_sitemap(sm, max_pokusaja=6)
            if xml_tekst:
                parsiraj_xml_u_mapu(xml_tekst, sm, proizvodi)

    # Filtriranje: izbacujemo potvrđene CD/DVD/Blu-Ray artikle odmah
    filtrirani = {}
    izbaceno_ne_vinila = 0
    potvrdjeno_vinila = 0

    for link, meta in proizvodi.items():
        status_formata = provjeri_format_iz_teksta(link, meta["naslov"])
        if status_formata == "NE_VINIL":
            izbaceno_ne_vinila += 1
            continue
        if status_formata == "VINIL":
            potvrdjeno_vinila += 1
        meta["status_formata"] = status_formata
        filtrirani[link] = meta

    print(
        f"📊 Sitemap statistika: Ukupno URL-ova: {len(proizvodi)} | "
        f"Odbijeno CD/DVD/Blu-Ray: {izbaceno_ne_vinila} | "
        f"Preostalo za provjeru: {len(filtrirani)} (Od toga potvrđenih LP-a iz XML-a: {potvrdjeno_vinila})",
        flush=True
    )
    return filtrirani


def parsiraj_xml_u_mapu(xml_tekst, sm_url, proizvodi):
    # Regex parsiranje <url>...</url> blokova otporno na XML namespace (image:loc / image:title)
    url_blokovi = re.findall(r"<url\b[^>]*>(.*?)</url>", xml_tekst, flags=re.DOTALL | re.IGNORECASE)
    print(f"   ✅ Uspješno izvučeno {len(url_blokovi)} <url> zapisa iz {sm_url}", flush=True)

    for blok in url_blokovi:
        # Glavni <loc> proizvoda (prije <image:image> bloka)
        glavni_dio = blok.split("<image:image")[0]
        m_loc = re.search(r"<loc>\s*(.*?)\s*</loc>", glavni_dio, flags=re.DOTALL | re.IGNORECASE)
        if not m_loc:
            continue

        puni_link = normaliziraj_link_proizvoda(m_loc.group(1))
        if not puni_link or puni_link == SITE_URL:
            continue
        if any(x in puni_link.lower() for x in ["/kosarica", "/korpa", "/prijava", "/registracija", "/checkout", "/cart", "/kontakt", "/o-nama", "/glazba/"]):
            continue

        m_img_loc = re.search(r"<image:loc>\s*(.*?)\s*</image:loc>", blok, flags=re.DOTALL | re.IGNORECASE)
        m_img_title = re.search(r"<image:title>\s*(.*?)\s*</image:title>", blok, flags=re.DOTALL | re.IGNORECASE)

        img_url = ocisti_url_slike(m_img_loc.group(1)) if m_img_loc else ""
        img_title = ocisti_cdata(m_img_title.group(1)) if m_img_title else ""

        if puni_link not in proizvodi:
            proizvodi[puni_link] = {"slika": img_url, "naslov": img_title}
        else:
            if img_url and not proizvodi[puni_link]["slika"]:
                proizvodi[puni_link]["slika"] = img_url
            if img_title and not proizvodi[puni_link]["naslov"]:
                proizvodi[puni_link]["naslov"] = img_title


def izvuci_cijenu_proizvoda(soup, forma_kosarica):
    # 1. Prvo tražimo cijenu isključivo unutar forme za košaricu samog proizvoda ili glavnog info bloka
    kandidati = []
    if forma_kosarica:
        kandidati.extend(forma_kosarica.select("span.price-current, .product-card-current-price, .current-price"))

    glavni_blok = soup.select_one("div.product-details, div.product-info, div.product-main, div.product-page")
    if glavni_blok:
        kandidati.extend(glavni_blok.select("span.price-current, .product-card-current-price, .current-price"))

    # 2. Specifična klasa span.price-current na razini stranice (nikada općeniti .price koji hvata poklon bon od 50 €!)
    kandidati.extend(soup.select("span.price-current, .product-card-current-price"))

    for el in kandidati:
        tekst = el.get_text(" ", strip=True).replace(".", "").replace(",", ".")
        m = re.search(r"(\d+(?:\.\d{1,2})?)", tekst)
        if m:
            try:
                val = float(m.group(1))
                if val > 0:
                    return f"{val:.2f}"
            except Exception:
                pass

    # 3. Provjera strukturiranih meta tagova ako postoje
    meta_price = soup.select_one('meta[property="product:price:amount"], meta[itemprop="price"]')
    if meta_price and meta_price.get("content"):
        tekst = meta_price["content"].strip().replace(",", ".")
        m = re.search(r"(\d+(?:\.\d{1,2})?)", tekst)
        if m:
            try:
                val = float(m.group(1))
                if val > 0:
                    return f"{val:.2f}"
            except Exception:
                pass

    return "0.00"


def je_li_potvrdjeno_vinil_na_stranici(soup, link, naslov, prethodni_status):
    # Provjera kategorije u breadcrumbsima / navigaciji proizvoda
    for a in soup.select('a[href*="/glazba/"]'):
        href_low = a.get("href", "").lower()
        # Ako je u breadcrumbsima označena kategorija CD/DVD, a nije LP
        if any(c in href_low for c in ["/glazba-cd", "/film-dvd", "/film-blu-ray"]):
            pass

    page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
    status_iz_titlea = provjeri_format_iz_teksta(link, page_title)
    if status_iz_titlea == "NE_VINIL":
        return False
    if status_iz_titlea == "VINIL" or prethodni_status == "VINIL":
        return True

    # Za artikle bez oznake u URL-u (npr. 3-...) tražimo potvrdu u tekstu specifikacija ili naslovu stranice
    status_iz_naslova = provjeri_format_iz_teksta(link, naslov)
    if status_iz_naslova == "NE_VINIL":
        return False
    if status_iz_naslova == "VINIL":
        return True

    # Provjera piše li igdje u opisu/detaljima proizvoda da je format LP / Vinyl
    detalji_el = soup.select_one("div.product-details, div.product-info, div.product-description, table")
    if detalji_el:
        detalji_tekst = detalji_el.get_text(" ", strip=True).upper()
        if re.search(r"\b(FORMAT\s*:\s*CD|AUDIO CD|COMPACT DISC)\b", detalji_tekst):
            return False
        if re.search(r"\b(LP|VINYL|VINIL|180G|GRAMOFONSKA PLOČA)\b", detalji_tekst):
            return True

    return False


def parsiraj_proizvod(item):
    link, meta = item
    html_tekst = sigurni_get_proizvod(link, timeout=25)
    if not html_tekst:
        return None

    soup = BeautifulSoup(html_tekst, "lxml")

    # 1. Provjera dostupnosti (mora imati aktivnu formu za košaricu bez disabled gumba)
    forma_kosarica = soup.select_one('form.item-cart, form[action*="add_to_cart"]')
    if not forma_kosarica:
        return None
    if forma_kosarica.select_one("button[disabled], input[disabled], .disabled"):
        return None

    # 2. Izvlačenje naslova
    naslov = meta.get("naslov", "").strip()
    if not naslov:
        performer_el = soup.select_one(".product-performer-wrapper, .artist")
        title_el = soup.select_one("h1.product-title, .product-info h1, .product-info h3, h1")
        izvodjac = html.unescape(performer_el.get_text(" ", strip=True)).strip() if performer_el else ""
        album = html.unescape(title_el.get_text(" ", strip=True)).strip() if title_el else ""
        if izvodjac and album and izvodjac.lower() not in album.lower():
            naslov = f"{izvodjac} - {album}"
        else:
            naslov = album or izvodjac

    if not naslov:
        return None

    # 3. Provjera je li zaista vinil (LP / Singl)
    if not je_li_potvrdjeno_vinil_na_stranici(soup, link, naslov, meta.get("status_formata", "NEPOZNATO")):
        return None

    # 4. Točna cijena proizvoda (bez lažnih 50.00 € iz headera)
    cijena = izvuci_cijenu_proizvoda(soup, forma_kosarica)
    if cijena == "0.00":
        return None

    # 5. Slika proizvoda (prvo čisti URL iz <image:loc> u sitemapu, zatim og:image ili /Documents/Products/)
    slika_url = meta.get("slika", "").strip()
    if not slika_url:
        og_img = soup.select_one('meta[property="og:image"]')
        if og_img and og_img.get("content"):
            slika_url = ocisti_url_slike(og_img["content"])

    if not slika_url:
        for img_el in soup.select('img[src*="/Documents/Products/"], img[data-src*="/Documents/Products/"], div.product-image img, a.product-display-grid-image img'):
            for attr in ["src", "data-src", "data-lazy-src"]:
                kandidat = ocisti_url_slike(img_el.get(attr, ""))
                if kandidat:
                    slika_url = kandidat
                    break
            if slika_url:
                break

    naslov_low = naslov.lower()
    link_low = link.lower()
    tip_artikla = 'Singl 7"' if ('7"' in naslov_low or "singl" in naslov_low or "-singl" in link_low) else "LP"

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
                    norm_k = normaliziraj_link_proizvoda(row[2])
                    row[2] = norm_k
                    sve_ploce[norm_k] = row
        print(f"📦 Učitano {len(sve_ploce)} postojećih zapisa iz {CSV_FILENAME}.", flush=True)

    print("🗺️ Pokrećem dohvat i parsiranje Cedeterija XML sitemapa...", flush=True)
    proizvodi_dict = dohvati_podatke_iz_sitemapa()

    if not proizvodi_dict:
        print("❌ Nema pronađenih stavki u sitemapima. Prekidam kako ne bih obrisao postojeću bazu.", flush=True)
        return

    zadaci = list(proizvodi_dict.items())
    ukupno_zadataka = len(zadaci)
    novi_zapisi = {}
    uspjesno = 0

    print(f"⚡ Provjeravam dostupnost, format i točne cijene za {ukupno_zadataka} artikala ({BROJ_NITI} niti)...", flush=True)

    velicina_bloka = 150
    with ThreadPoolExecutor(max_workers=BROJ_NITI) as executor:
        for i in range(0, ukupno_zadataka, velicina_bloka):
            isjecak = zadaci[i:i + velicina_bloka]
            t_blok = time.time()
            rezultati = list(executor.map(parsiraj_proizvod, isjecak))

            novih_u_bloku = 0
            for res in rezultati:
                if res:
                    link, red = res
                    novi_zapisi[link] = red
                    sve_ploce[link] = red
                    uspjesno += 1
                    novih_u_bloku += 1

            obradjeno = min(i + velicina_bloka, ukupno_zadataka)
            print(
                f"   ⏳ Napredak: {obradjeno}/{ukupno_zadataka} "
                f"({time.time() - t_blok:.1f}s) | +{novih_u_bloku} dostupnih LP-a | Ukupno LP-a: {uspjesno}",
                flush=True
            )

    print(f"\n✅ Obrada završena! Ukupno pronađeno dostupnih vinila: {uspjesno}", flush=True)

    # Sinkronizacija: ako smo uspješno prošli katalog, mičemo rasprodane i stare CD zapise
    aktivni_linkovi = set(novi_zapisi.keys())
    if len(aktivni_linkovi) > 50:
        pocetni_broj = len(sve_ploce)
        sve_ploce = {k: v for k, v in sve_ploce.items() if k in aktivni_linkovi}
        obrisano = pocetni_broj - len(sve_ploce)
        print(f"🧹 Sinkronizacija: Uklonjeno {obrisano} rasprodanih ili ne-vinilnih artikala iz baze.", flush=True)

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
