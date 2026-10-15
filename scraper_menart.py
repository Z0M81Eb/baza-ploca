from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup
import csv
import os
import re
import time
import html
from urllib.parse import urljoin

SITE_URL = "https://menartshop.hr"
CSV_FILENAME = "menart_katalog.csv"

VINYL_ARCHIVES = [
    ("https://menartshop.hr/format-glazba/lp/", "LP"),
    ("https://menartshop.hr/format-glazba/lp-cd/", "LP+CD"),
    ("https://menartshop.hr/format-glazba/cd-lp/", "CD+LP"),
    ("https://menartshop.hr/format-glazba/lp-dvd/", "LP+DVD")
]

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "hr-HR,hr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://menartshop.hr/"
}

def kreiraj_sesiju():
    session = cffi_requests.Session(impersonate="chrome120")
    session.headers.update(HEADERS)
    try:
        session.get(SITE_URL, timeout=20)
        time.sleep(1)
    except Exception:
        pass
    return session

def ocisti_url_slike(raw_url):
    if not raw_url:
        return ""
    val = raw_url.strip()
    if val.startswith('data:image') or 'placeholder' in val.lower() or 'blank.' in val.lower():
        return ""
    # Ako je srcset (npr. "https://.../slika.jpg 300w, ..."), uzmi samo prvi URL
    if ' ' in val or ',' in val:
        val = val.split(',')[0].strip().split(' ')[0].strip()
    if val.startswith('//'):
        return 'https:' + val
    if val.startswith('/'):
        return urljoin(SITE_URL, val)
    return val

def izvuci_sliku_iz_elementa(el):
    if not el:
        return ""

    # 1. Provjeri sve <img> oznake unutar elementa
    for img in el.find_all('img'):
        for attr in ['data-lazy-src', 'data-src', 'data-original', 'data-large_image', 'src']:
            url = ocisti_url_slike(img.get(attr, ''))
            if url:
                return url
        for attr in ['data-lazy-srcset', 'data-srcset', 'srcset']:
            url = ocisti_url_slike(img.get(attr, ''))
            if url:
                return url

    # 2. Provjeri <source> oznake (ako koriste <picture> tag)
    for source in el.find_all('source'):
        for attr in ['data-srcset', 'srcset', 'src']:
            url = ocisti_url_slike(source.get(attr, ''))
            if url:
                return url

    # 3. Provjeri <noscript> (često kod WP-Rocket / LiteSpeed lazy loada)
    raw_html = str(el)
    ns_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', raw_html, re.IGNORECASE)
    if ns_match:
        url = ocisti_url_slike(ns_match.group(1))
        if url:
            return url

    # 4. Provjeri CSS background-image
    bg_match = re.search(r'background-image\s*:\s*url\(["\']?([^"\')]+)["\']?\)', raw_html, re.IGNORECASE)
    if bg_match:
        url = ocisti_url_slike(bg_match.group(1))
        if url:
            return url

    return ""

def izgradi_mapu_slika_na_stranici(soup):
    """Mapira svaki link proizvoda (/proizvod/...) s pripadajućom slikom bilo gdje na stranici."""
    mapa_slika = {}
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        if '/proizvod/' not in href or '?add-to-cart=' in href:
            continue
        norm_link = href.rstrip('/')
        if norm_link in mapa_slika and mapa_slika[norm_link]:
            continue

        # Provjeri je li slika direktno unutar ovog <a> linka
        slika = izvuci_sliku_iz_elementa(a)
        if not slika and a.parent:
            # Provjeri roditeljski element
            slika = izvuci_sliku_iz_elementa(a.parent)
        if not slika and a.parent and a.parent.parent:
            # Provjeri zajednički okvir kartice (2 razine iznad)
            slika = izvuci_sliku_iz_elementa(a.parent.parent)

        if slika:
            mapa_slika[norm_link] = slika
    return mapa_slika

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

def parsiraj_kartice_na_stranici(soup, default_format):
    pronadjeno = []
    mapa_slika = izgradi_mapu_slika_na_stranici(soup)

    kartice = soup.select('li.product, div.product, article.product, .type-product, .product-item, .product-grid-item')

    if not kartice:
        vidjeni_roditelji = set()
        for a in soup.select('a[href*="/proizvod/"]'):
            # Penjemo se do okvira koji sadrži i cijenu/košaricu i sliku
            kandidat = a
            for _ in range(4):
                if kandidat.parent and kandidat.parent.name in ['li', 'article', 'div']:
                    kandidat = kandidat.parent
                    t = kandidat.get_text(" ", strip=True)
                    if '€' in t and ('košaric' in t.lower() or 'pročitaj' in t.lower() or 'rasprodano' in t.lower()):
                        break
            if kandidat and id(kandidat) not in vidjeni_roditelji:
                vidjeni_roditelji.add(id(kandidat))
                kartice.append(kandidat)

    vidjeni_linkovi_na_str = set()

    for k in kartice:
        klase = " ".join(k.get('class', [])).lower()
        tekst_kartice = k.get_text(" ", strip=True)
        tekst_lower = tekst_kartice.lower()

        if 'outofstock' in klase:
            continue
        if 'dodaj u košaricu' not in tekst_lower and 'add_to_cart' not in str(k).lower():
            continue

        link = ""
        for a in k.find_all('a', href=True):
            href = a['href'].strip()
            if '/proizvod/' in href and '?add-to-cart=' not in href:
                link = href
                break
        if not link or link in vidjeni_linkovi_na_str:
            continue

        naslov_el = k.select_one('.woocommerce-loop-product__title, .product-title, h2, h3, h4')
        if naslov_el:
            naslov = naslov_el.get_text(" ", strip=True)
        else:
            naslov = ""
            for a in k.find_all('a', href=True):
                if '/proizvod/' in a['href']:
                    t = a.get_text(" ", strip=True)
                    if t and 'dodaj u košaricu' not in t.lower() and 'pogledaj' not in t.lower():
                        naslov = t
                        break

        naslov = html.unescape(naslov).strip()
        if not naslov:
            continue

        cijena_el = k.select_one('.price ins, .price')
        cijena = izvuci_cijenu(cijena_el.get_text(" ", strip=True) if cijena_el else tekst_kartice)
        if cijena == "0.00":
            continue

        # Izvlačenje slike (iz kartice, iz mape stranice ili iz roditeljskog okvira)
        norm_link = link.rstrip('/')
        slika_url = izvuci_sliku_iz_elementa(k)
        if not slika_url:
            slika_url = mapa_slika.get(norm_link, '')
        if not slika_url and k.parent:
            slika_url = izvuci_sliku_iz_elementa(k.parent)

        vidjeni_linkovi_na_str.add(link)
        pronadjeno.append((link, [naslov, cijena, link, slika_url, "Novo", "Novo", default_format]))

    return pronadjeno

def scrape_menart_html():
    sve_ploce = {}
    vidjeni_linkovi = set()
    uspjesno_skenirano = False

    if os.path.exists(CSV_FILENAME):
        with open(CSV_FILENAME, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) >= 7:
                    sve_ploce[row[2]] = row
        print(f"📦 Učitano {len(sve_ploce)} postojećih ploča iz memorije.", flush=True)

    session = kreiraj_sesiju()
    print("\n🚀 Pokrećem HTML skeniranje Menart vinilnih arhiva...", flush=True)

    for base_url, format_oznaka in VINYL_ARCHIVES:
        print(f"\n📂 Skeniram arhivu: {base_url} ({format_oznaka})", flush=True)
        page = 1
        praznih_zaredom = 0

        while True:
            url = base_url if page == 1 else f"{base_url}page/{page}/"
            print(f"📄 Stranica {page}...", end=" ", flush=True)

            try:
                res = session.get(url, timeout=25)
                if res.status_code == 404:
                    print("-> Kraj arhive (404).", flush=True)
                    if page > 1 and format_oznaka == "LP":
                        uspjesno_skenirano = True
                    break
                elif res.status_code != 200:
                    print(f"-> [HTTP Status: {res.status_code}]", flush=True)
                    break

                if page > 1 and res.url.rstrip('/') == base_url.rstrip('/'):
                    print("-> Preusmjereno na početak (kraj arhive).", flush=True)
                    if format_oznaka == "LP":
                        uspjesno_skenirano = True
                    break

                soup = BeautifulSoup(res.text, 'lxml')
                stavke = parsiraj_kartice_na_stranici(soup, format_oznaka)

                if not stavke:
                    ima_bilo_kakvih = len(soup.select('a[href*="/proizvod/"]')) > 0
                    if not ima_bilo_kakvih:
                        print("-> Nema više proizvoda. Kraj arhive.", flush=True)
                        if page > 1 and format_oznaka == "LP":
                            uspjesno_skenirano = True
                        break
                    else:
                        print("-> 0 na zalihi na ovoj stranici.", flush=True)
                        praznih_zaredom += 1
                        if praznih_zaredom >= 5:
                            uspjesno_skenirano = True
                            break
                else:
                    praznih_zaredom = 0
                    sa_slikom = 0
                    for link, red_podataka in stavke:
                        # Ako slučajno nova slika nije pronađena, zadrži staru sliku iz CSV-a ako postoji
                        if not red_podataka[3] and link in sve_ploce and len(sve_ploce[link]) >= 4:
                            red_podataka[3] = sve_ploce[link][3]
                        if red_podataka[3]:
                            sa_slikom += 1
                        sve_ploce[link] = red_podataka
                        vidjeni_linkovi.add(link)
                    print(f"-> Pronađeno {len(stavke)} vinila (Sa slikom: {sa_slikom}/{len(stavke)}).", flush=True)

                page += 1
                time.sleep(0.5)

            except Exception as e:
                print(f"\n❌ Greška na stranici {page}: {e}", flush=True)
                break

    if uspjesno_skenirano and len(vidjeni_linkovi) > 100:
        pocetni_broj = len(sve_ploce)
        sve_ploce = {k: v for k, v in sve_ploce.items() if k in vidjeni_linkovi}
        obrisano = pocetni_broj - len(sve_ploce)
        print(f"\n🧹 Analiza završena. Obrisano {obrisano} rasprodanih ploča.", flush=True)
    else:
        print(f"\n⚠️ Pronađeno ukupno {len(vidjeni_linkovi)} ploča. Zadržavam postojeće u bazi.", flush=True)

    if sve_ploce:
        zaglavlja = ["Naslov", "Cijena", "URL_Proizvoda", "URL_Slike", "Stanje_Medija", "Stanje_Omota", "Tip_Artikla"]
        with open(CSV_FILENAME, mode='w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(zaglavlja)
            writer.writerows(sve_ploce.values())
        ukupno_slika = sum(1 for v in sve_ploce.values() if len(v) >= 4 and v[3])
        print(f"🎉 GOTOVO! U bazi je ukupno {len(sve_ploce)} ploča (od toga sa slikom: {ukupno_slika}).", flush=True)

if __name__ == "__main__":
    scrape_menart_html()
