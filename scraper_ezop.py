import cloudscraper
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context
import csv
import os
import time
import html

class TlsAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        context = create_urllib3_context()
        context.set_ciphers('DEFAULT@SECLEVEL=1')
        kwargs['ssl_context'] = context
        return super(TlsAdapter, self).init_poolmanager(*args, **kwargs)

API_BASE = "https://ezop-antikvarijat.hr/wp-json/wc/store"
CSV_FILENAME = "ezop_ploce.csv"

def pretvori_ocjenu(ocjena_str):
    if not ocjena_str:
        return ""
    tekst = str(ocjena_str).strip().upper()
    if tekst in ["M", "NM", "VG+", "VG", "G+", "G", "F", "P", "NOVO"]:
        return tekst
    try:
        ocjena = int(float(tekst))
        mape = {10: "M", 9: "NM", 8: "VG+", 7: "VG", 6: "G+", 5: "G"}
        return mape.get(ocjena, "VG")
    except:
        return tekst

def get_glazba_category_id(scraper):
    print("Tražim ID kategorije 'Glazba'...", flush=True)
    try:
        res = scraper.get(f"{API_BASE}/products/categories?per_page=100", timeout=20)
        if res.status_code == 200:
            for cat in res.json():
                if cat.get('slug') == 'glazba':
                    print(f"Pronađena kategorija Glazba (ID: {cat['id']})", flush=True)
                    return cat['id']
    except Exception as e:
        print(f"Upozorenje pri dohvaćanju kategorija: {e}", flush=True)
    return None

def scrape_ezop_api():
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
        print(f"Učitano {len(sve_ploce)} postojećih ploča iz memorije.", flush=True)

    scraper = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
    )
    scraper.mount('https://', TlsAdapter())

    cat_id = get_glazba_category_id(scraper)
    page = 1
    per_page = 50
    max_retries = 3

    print("\n=== POKREĆEM EZOP API SKENIRANJE ===", flush=True)

    while True:
        if cat_id:
            url = f"{API_BASE}/products?category={cat_id}&page={page}&per_page={per_page}"
        else:
            url = f"{API_BASE}/products?page={page}&per_page={per_page}"

        print(f"Preuzimam API paket {page}...", end=" ", flush=True)

        pokusaj = 0
        uspjeh_paket = False
        data = None

        while pokusaj < max_retries:
            try:
                res = scraper.get(url, timeout=30)
                if res.status_code == 400:
                    print("-> Kraj arhive dostignut.", flush=True)
                    uspjesno_skenirano = True
                    uspjeh_paket = True
                    break
                elif res.status_code != 200:
                    print(f"[Kod: {res.status_code}] Ponovni pokušaj...", end=" ", flush=True)
                    pokusaj += 1
                    time.sleep(5)
                    continue

                data = res.json()
                if not data:
                    print("-> Prazan paket. Kraj arhive.", flush=True)
                    uspjesno_skenirano = True
                    uspjeh_paket = True
                    break

                uspjeh_paket = True
                break
            except Exception as e:
                print(f"[Greška mreže] Ponovni pokušaj...", end=" ", flush=True)
                pokusaj += 1
                time.sleep(5)

        if not uspjeh_paket or res.status_code == 400 or not data:
            break

        broj_ploca_u_paketu = 0

        for item in data:
            if not item.get('is_in_stock', False):
                continue

            is_vinyl = False
            s_omot = ""
            s_medij = ""

            # 1. Provjera u variations
            for var in item.get('variations', []):
                for attr in var.get('attributes', []):
                    ime_attr = attr.get('name', '').lower()
                    vrijednost = attr.get('value', '')
                    if 'omot' in ime_attr:
                        s_omot = pretvori_ocjenu(vrijednost)
                        is_vinyl = True
                    elif 'medij' in ime_attr or 'ploč' in ime_attr or 'ploc' in ime_attr:
                        s_medij = pretvori_ocjenu(vrijednost)
                        is_vinyl = True

            # 2. Provjera u top-level attributes
            for attr in item.get('attributes', []):
                ime_attr = attr.get('name', '').lower()
                terms = attr.get('terms', [])
                vrijednost = terms[0].get('name', '') if terms else ''
                if 'omot' in ime_attr and not s_omot:
                    s_omot = pretvori_ocjenu(vrijednost)
                    is_vinyl = True
                elif ('medij' in ime_attr or 'stanje' in ime_attr) and not s_medij:
                    s_medij = pretvori_ocjenu(vrijednost)
                    is_vinyl = True

            # 3. Provjera po kategorijama ako je u kategoriji gramofonske ploče / vinil
            kategorije = [k.get('slug', '').lower() for k in item.get('categories', [])]
            if any(w in k for k in kategorije for w in ['gramofonske', 'ploce', 'vinil', 'vinyl', 'lp']):
                is_vinyl = True

            if not is_vinyl:
                continue

            if not s_medij:
                s_medij = "VG+"
            if not s_omot:
                s_omot = s_medij

            naslov = html.unescape(item.get('name', '')).strip()
            link = item.get('permalink', '')

            prices = item.get('prices', {})
            raw_price = prices.get('price', '0')
            minor_unit = prices.get('currency_minor_unit', 2)
            try:
                price_val = float(raw_price) / (10 ** minor_unit)
                cijena = f"{price_val:.2f}"
            except:
                cijena = "0.00"

            images = item.get('images', [])
            slika_url = images[0].get('src', '') if images else ""

            if naslov and cijena != "0.00" and link:
                sve_ploce[link] = [naslov, cijena, link, slika_url, s_medij, s_omot, "Vinil"]
                vidjeni_linkovi.add(link)
                broj_ploca_u_paketu += 1

        print(f"-> Pronađeno ploča: {broj_ploca_u_paketu}", flush=True)
        page += 1
        time.sleep(0.5)

    # Brisanje prodanih SAMO ako je skeniranje prošlo do kraja i pronašlo barem 50 ploča
    if uspjesno_skenirano and len(vidjeni_linkovi) > 50:
        pocetni_broj = len(sve_ploce)
        sve_ploce = {k: v for k, v in sve_ploce.items() if k in vidjeni_linkovi}
        obrisano = pocetni_broj - len(sve_ploce)
        print(f"\nAnaliza završena. Obrisano {obrisano} prodanih ploča.", flush=True)
    else:
        print("\nUPOZORENJE: Preskačem brisanje starih ploča radi sigurnosti baze.", flush=True)

    if sve_ploce:
        with open(CSV_FILENAME, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Naslov', 'Cijena', 'URL_Proizvoda', 'URL_Slike', 'Stanje_Medija', 'Stanje_Omota', 'Tip_Artikla'])
            writer.writerows(sve_ploce.values())
        print(f"Završeno. U bazi je sada ukupno {len(sve_ploce)} ažurnih ploča.", flush=True)

if __name__ == "__main__":
    scrape_ezop_api()
