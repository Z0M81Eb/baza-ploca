from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup
import csv
import os
import re
import time
import html

CSV_FILENAME = "menart_katalog.csv"

# Direktne Menart arhive u kojima se nalaze isključivo vinilna izdanja
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
        session.get("https://menartshop.hr/", timeout=20)
        time.sleep(1)
    except Exception:
        pass
    return session

def izvuci_cijenu(tekst):
    """Pronalazi zadnju (prodajnu/akcijsku) cijenu u formatu 24,95 € ili 24.95 €."""
    svi_brojevi = re.findall(r'(\d{1,4}[.,]\d{2})\s*€', tekst)
    if not svi_brojevi:
        svi_brojevi = re.findall(r'(\d{1,4}[.,]\d{2})', tekst)
    if svi_brojevi:
        zadnja = svi_brojevi[-1].replace('.', '').replace(',', '.')
        # Ako je slučajno imalo decimalnu točku prije zamjene
        dijelovi = svi_brojevi[-1].replace(',', '.').split('.')
        if len(dijelovi) == 2:
            zadnja = f"{dijelovi[0]}.{dijelovi[1]}"
        try:
            val = float(zadnja)
            if val > 0:
                return f"{val:.2f}"
        except Exception:
            pass
    return "0.00"

def izvuci_sliku(kartica):
    img = kartica.find('img')
    if not img:
        return ""
    for attr in ['data-lazy-src', 'data-src', 'src']:
        val = img.get(attr, '').strip()
        if val and not val.startswith('data:image'):
            return val
    srcset = img.get('srcset', '') or img.get('data-lazy-srcset', '')
    if srcset:
        prvi = srcset.split(',')[0].strip().split(' ')[0]
        if prvi and not prvi.startswith('data:image'):
            return prvi
    return ""

def parsiraj_kartice_na_stranici(soup, default_format):
    pronadjeno = []
    # Standardni WooCommerce selektori + fallback na sve kartice s linkom na /proizvod/
    kartice = soup.select('li.product, div.product, article.product, .type-product')

    if not kartice:
        # Fallback: pronađi roditeljske elemente oko linkova na /proizvod/
        vidjeni_roditelji = set()
        for a in soup.select('a[href*="/proizvod/"]'):
            roditelj = a.find_parent(['li', 'article', 'div'])
            if roditelj and id(roditelj) not in vidjeni_roditelji:
                vidjeni_roditelji.add(id(roditelj))
                kartice.append(roditelj)

    for k in kartice:
        klase = " ".join(k.get('class', [])).lower()
        tekst_kartice = k.get_text(" ", strip=True)
        tekst_lower = tekst_kartice.lower()

        # 1. Provjera zalihe: artikl mora imati "Dodaj u košaricu" i ne smije biti outofstock
        if 'outofstock' in klase:
            continue
        if 'dodaj u košaricu' not in tekst_lower and 'add_to_cart' not in str(k).lower():
            continue

        # 2. Link proizvoda
        link = ""
        for a in k.find_all('a', href=True):
            href = a['href'].strip()
            if '/proizvod/' in href and '?add-to-cart=' not in href:
                link = href
                break
        if not link:
            continue

        # 3. Naslov proizvoda
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

        # 4. Cijena proizvoda
        cijena_el = k.select_one('.price ins, .price')
        cijena = izvuci_cijenu(cijena_el.get_text(" ", strip=True) if cijena_el else tekst_kartice)
        if cijena == "0.00":
            continue

        # 5. Slika proizvoda
        slika_url = izvuci_sliku(k)

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
    print("\n🚀 Pokrećem HTML skeniranje Menart vinilnih arhiva (API zaobiđen)...", flush=True)

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

                # Ako nas je preusmjerilo natrag na 1. stranicu, došli smo do kraja
                if page > 1 and res.url.rstrip('/') == base_url.rstrip('/'):
                    print("-> Preusmjereno na početak (kraj arhive).", flush=True)
                    if format_oznaka == "LP":
                        uspjesno_skenirano = True
                    break

                soup = BeautifulSoup(res.text, 'lxml')
                stavke = parsiraj_kartice_na_stranici(soup, format_oznaka)

                if not stavke:
                    # Provjeri postoje li uopće ikakvi proizvodi na stranici (možda su svi rasprodani na ovoj stranici)
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
                    novih_na_str = 0
                    for link, red_podataka in stavke:
                        sve_ploce[link] = red_podataka
                        if link not in vidjeni_linkovi:
                            novih_na_str += 1
                        vidjeni_linkovi.add(link)
                    print(f"-> Pronađeno {len(stavke)} aktivnih vinila.", flush=True)

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
        print(f"\n⚠️ Pronađeno ukupno {len(vidjeni_linkovi)} ploča. Zadržavam postojeće u bazi radi sigurnosti.", flush=True)

    if sve_ploce:
        zaglavlja = ["Naslov", "Cijena", "URL_Proizvoda", "URL_Slike", "Stanje_Medija", "Stanje_Omota", "Tip_Artikla"]
        with open(CSV_FILENAME, mode='w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(zaglavlja)
            writer.writerows(sve_ploce.values())
        print(f"🎉 GOTOVO! U bazi je sada ukupno {len(sve_ploce)} ploča.", flush=True)

if __name__ == "__main__":
    scrape_menart_html()
