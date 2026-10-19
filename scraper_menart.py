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

def normaliziraj_link_proizvoda(href):
    if not href:
        return ""
    puni = urljoin(SITE_URL, href.strip())
    return puni.split('?')[0].rstrip('/') + '/'

def ocisti_url_slike(raw_url, is_srcset=False):
    if not raw_url:
        return ""
    val = html.unescape(str(raw_url)).strip()
    val_lower = val.lower()

    if val_lower.startswith('data:image') or any(x in val_lower for x in ['placeholder', 'blank.', 'spinner', 'loader', '.svg', '1x1', 'emoji', 'gravatar']):
        return ""

    if is_srcset:
        # Kod srcseta (npr. "https://.../slika.jpg 300w, https://.../slika2.jpg 600w")
        # Režemo isključivo prema oznaci širine/gustoće (npr. " 300w" ili " 2x"), a ne po imenu datoteke!
        kandidati = re.split(r'\s+\d+[wx]\s*,?\s*', val)
        kandidati = [k.strip().rstrip(',') for k in kandidati if k.strip()]
        if not kandidati:
            return ""
        # Uzimamo prvi kandidat (ili zadnji ako želimo veću rezoluciju)
        val = kandidati[0]
        if ' ' in val and re.search(r'\s+\d+[wx]$', val):
            val = re.sub(r'\s+\d+[wx]$', '', val).strip()

    if val.startswith('//'):
        val = 'https:' + val
    elif val.startswith('/'):
        val = urljoin(SITE_URL, val)

    if not val.startswith('http'):
        return ""

    # Zamijeni razmake u nazivu datoteke sa %20 kako URL ne bi puknuo u WordPressu
    val = val.replace(' ', '%20')
    return val

def izvuci_sliku_iz_elementa(el):
    if not el:
        return ""

    imgs = [el] if getattr(el, 'name', None) == 'img' else el.find_all('img')

    # 1. Provjeri sve poznate lazy-load i standardne atribute na <img> oznakama
    lazy_attrs = [
        'data-lazy-src', 'data-src', 'data-original', 'data-large_image',
        'data-opt-src', 'nitro-lazy-src', 'data-breeze', 'data-wood-src',
        'data-src-img', 'data-echo', 'src'
    ]
    srcset_attrs = ['data-lazy-srcset', 'data-srcset', 'srcset']

    for img in imgs:
        for attr in lazy_attrs:
            url = ocisti_url_slike(img.get(attr, ''), is_srcset=False)
            if url:
                return url
        for attr in srcset_attrs:
            url = ocisti_url_slike(img.get(attr, ''), is_srcset=True)
            if url:
                return url
        # Provjeri i sve ostale atribute na <img> ako sadrže wp-content/uploads
        for attr_name, attr_val in img.attrs.items():
            if isinstance(attr_val, str) and '/wp-content/uploads/' in attr_val:
                is_ss = 'srcset' in attr_name.lower()
                url = ocisti_url_slike(attr_val, is_srcset=is_ss)
                if url:
                    return url

    # 2. Provjeri <source> oznake (unutar <picture> elementa)
    for source in el.find_all('source'):
        for attr in ['data-srcset', 'srcset']:
            url = ocisti_url_slike(source.get(attr, ''), is_srcset=True)
            if url:
                return url
        url = ocisti_url_slike(source.get('src', ''), is_srcset=False)
        if url:
            return url

    # 3. Provjeri sve <img> oznake u sirovom HTML-u (hvata <noscript> slike koje lxml drži kao tekst!)
    raw_html = str(el)
    for m in re.finditer(r'<img[^>]+(?:data-lazy-src|data-src|src)=["\']([^"\']+)["\']', raw_html, re.IGNORECASE):
        url = ocisti_url_slike(m.group(1), is_srcset=False)
        if url:
            return url

    # 4. Provjeri CSS background-image
    for m in re.finditer(r'background-image\s*:\s*url\(["\']?([^"\')]+)["\']?\)', raw_html, re.IGNORECASE):
        url = ocisti_url_slike(m.group(1), is_srcset=False)
        if url:
            return url

    # 5. Direktni ulov bilo kojeg /wp-content/uploads/ linka slike unutar HTML bloka kartice
    upload_match = re.search(r'(https?://[^"\'\s<>)]+/wp-content/uploads/[^"\'\s<>)]+\.(?:jpg|jpeg|png|webp))', raw_html, re.IGNORECASE)
    if upload_match:
        url = ocisti_url_slike(upload_match.group(1), is_srcset=False)
        if url:
            return url

    return ""

def broj_razlicitih_proizvoda_u_elementu(el):
    linkovi = set()
    for a in el.find_all('a', href=True):
        h = a['href'].strip()
        if '/proizvod/' in h and '?add-to-cart=' not in h:
            linkovi.add(normaliziraj_link_proizvoda(h))
            if len(linkovi) > 1:
                return len(linkovi)
    return len(linkovi)

def pronadji_puni_okvir_kartice(start_el):
    """Penje se uz DOM stablo sve do najvišeg okvira koji i dalje pripada SAMO tom jednom proizvodu."""
    kandidat = start_el
    for _ in range(7):
        roditelj = kandidat.parent
        if not roditelj or getattr(roditelj, 'name', None) in ['body', 'main', 'html', '[document]']:
            break
        if broj_razlicitih_proizvoda_u_elementu(roditelj) == 1:
            kandidat = roditelj
        else:
            break
    return kandidat

def izgradi_mapu_slika_na_stranici(soup):
    mapa_slika = {}
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        if '/proizvod/' not in href or '?add-to-cart=' in href:
            continue
        norm_link = normaliziraj_link_proizvoda(href)
        if norm_link in mapa_slika and mapa_slika[norm_link]:
            continue

        okvir = pronadji_puni_okvir_kartice(a)
        slika = izvuci_sliku_iz_elementa(okvir)
        if slika:
            mapa_slika[norm_link] = slika
    return mapa_slika

def dohvati_sliku_sa_stranice_proizvoda(session, product_url):
    """Sigurnosni fallback: ako slika nije pronađena na arhivi, otvara sam proizvod i uzima og:image."""
    try:
        r = session.get(product_url, timeout=15)
        if r.status_code != 200:
            return ""
        s = BeautifulSoup(r.text, 'lxml')
        og = s.find('meta', property='og:image') or s.find('meta', attrs={'name': 'twitter:image'})
        if og and og.get('content'):
            url = ocisti_url_slike(og['content'], is_srcset=False)
            if url:
                return url
        galerija = s.select_one('.woocommerce-product-gallery, .product-images, .images, .product-main-image')
        if galerija:
            url = izvuci_sliku_iz_elementa(galerija)
            if url:
                return url
    except Exception:
        pass
    return ""

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
        vidjeni_okviri = set()
        for a in soup.select('a[href*="/proizvod/"]'):
            if '?add-to-cart=' in a.get('href', ''):
                continue
            okvir = pronadji_puni_okvir_kartice(a)
            if okvir and id(okvir) not in vidjeni_okviri:
                vidjeni_okviri.add(id(okvir))
                kartice.append(okvir)

    vidjeni_linkovi_na_str = set()

    for k in kartice:
        # Osiguraj da imamo puni vanjski okvir kartice ovog jednog proizvoda
        puni_k = pronadji_puni_okvir_kartice(k)
        klase = " ".join((k.get('class', []) or []) + (puni_k.get('class', []) or [])).lower()
        tekst_kartice = puni_k.get_text(" ", strip=True)
        tekst_lower = tekst_kartice.lower()

        if 'outofstock' in klase:
            continue
        if 'dodaj u košaricu' not in tekst_lower and 'add_to_cart' not in str(puni_k).lower():
            continue

        link = ""
        for a in puni_k.find_all('a', href=True):
            href = a['href'].strip()
            if '/proizvod/' in href and '?add-to-cart=' not in href:
                link = normaliziraj_link_proizvoda(href)
                break
        if not link or link in vidjeni_linkovi_na_str:
            continue

        naslov_el = puni_k.select_one('.woocommerce-loop-product__title, .product-title, h2, h3, h4')
        if naslov_el:
            naslov = naslov_el.get_text(" ", strip=True)
        else:
            naslov = ""
            for a in puni_k.find_all('a', href=True):
                if '/proizvod/' in a['href']:
                    t = a.get_text(" ", strip=True)
                    if t and 'dodaj u košaricu' not in t.lower() and 'pogledaj' not in t.lower():
                        naslov = t
                        break

        naslov = html.unescape(naslov).strip()
        if not naslov:
            continue

        cijena_el = puni_k.select_one('.price ins, .price')
        cijena = izvuci_cijenu(cijena_el.get_text(" ", strip=True) if cijena_el else tekst_kartice)
        if cijena == "0.00":
            continue

        slika_url = izvuci_sliku_iz_elementa(puni_k)
        if not slika_url:
            slika_url = mapa_slika.get(link, '')

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
                    norm_k = normaliziraj_link_proizvoda(row[2])
                    row[2] = norm_k
                    sve_ploce[norm_k] = row
        print(f"📦 Učitano {len(sve_ploce)} postojećih ploča iz memorije.", flush=True)

    session = kreiraj_sesiju()
    print("\n🚀 Pokrećem HTML skeniranje Menart vinilnih arhiva...", flush=True)
    ispisan_primjer_slike = False

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
                        # 1. Ako slika nije nađena na arhivi, provjeri imamo li već važeću sliku u starom CSV-u
                        if not red_podataka[3] and link in sve_ploce and len(sve_ploce[link]) >= 4:
                            stara_slika = ocisti_url_slike(sve_ploce[link][3], is_srcset=False)
                            if stara_slika:
                                red_podataka[3] = stara_slika

                        # 2. Ako je i dalje prazna, otvori pojedinačnu stranicu proizvoda i izvuci og:image
                        if not red_podataka[3]:
                            red_podataka[3] = dohvati_sliku_sa_stranice_proizvoda(session, link)

                        if red_podataka[3]:
                            sa_slikom += 1
                            if not ispisan_primjer_slike:
                                print(f"\n🖼️ Primjer uhvaćene slike: {red_podataka[3]}", end=" ", flush=True)
                                ispisan_primjer_slike = True

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
