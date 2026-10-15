from curl_cffi import requests as cffi_requests
import csv
import os
import time
import html

SITE_URL = "https://ezop-antikvarijat.hr"
CSV_FILENAME = "ezop_ploce.csv"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "hr-HR,hr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://ezop-antikvarijat.hr/",
    "Origin": "https://ezop-antikvarijat.hr"
}

API_ENDPOINTS = [
    ("https://ezop-antikvarijat.hr/wp-json/wc/store/products", "?"),
    ("https://ezop-antikvarijat.hr/wp-json/wc/store/v1/products", "?"),
    ("https://ezop-antikvarijat.hr/?rest_route=/wc/store/products", "&"),
    ("https://ezop-antikvarijat.hr/?rest_route=/wc/store/v1/products", "&")
]

CAT_ENDPOINTS = [
    "https://ezop-antikvarijat.hr/wp-json/wc/store/products/categories?per_page=100",
    "https://ezop-antikvarijat.hr/?rest_route=/wc/store/products/categories&per_page=100"
]

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
    except Exception:
        return tekst

def kreiraj_sesiju(profil="chrome120"):
    session = cffi_requests.Session(impersonate=profil)
    session.headers.update(HEADERS)
    try:
        session.get(SITE_URL, timeout=20)
        time.sleep(1.5)
    except Exception as e:
        print(f"Upozorenje pri otvaranju naslovnice: {e}", flush=True)
    return session

def get_glazba_category_id(session):
    print("Tražim ID kategorije 'Glazba'...", flush=True)
    for url in CAT_ENDPOINTS:
        try:
            res = session.get(url, timeout=20)
            if res.status_code == 200:
                for cat in res.json():
                    if cat.get('slug') == 'glazba':
                        print(f"Pronađena kategorija Glazba (ID: {cat['id']})", flush=True)
                        return cat['id']
        except Exception:
            continue
    print("Nastavljam bez filtera kategorije...", flush=True)
    return None

def dohvati_api_paket(session, page, per_page, cat_id, aktivni_endpoint_idx):
    profili = ["chrome120", "chrome116", "safari15_5"]

    for idx in range(aktivni_endpoint_idx, len(API_ENDPOINTS)):
        base_url, sep = API_ENDPOINTS[idx]
        if cat_id:
            url = f"{base_url}{sep}category={cat_id}&page={page}&per_page={per_page}"
        else:
            url = f"{base_url}{sep}page={page}&per_page={per_page}"

        for pokusaj, profil in enumerate(profili):
            try:
                if pokusaj > 0:
                    session = kreiraj_sesiju(profil)
                res = session.get(url, timeout=30)
                if res.status_code in [200, 400, 404]:
                    return res, session, idx
                print(f"[Kod: {res.status_code} na ulazu {idx+1}]", end=" ", flush=True)
                time.sleep(3)
            except Exception as e:
                print(f"[Greška mreže: {e}]", end=" ", flush=True)
                time.sleep(3)

    return None, session, aktivni_endpoint_idx

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

    session = kreiraj_sesiju("chrome120")
    cat_id = get_glazba_category_id(session)
    page = 1
    per_page = 50
    aktivni_endpoint_idx = 0

    print("\n=== POKREĆEM EZOP CHROME-TLS API SKENIRANJE ===", flush=True)

    while True:
        print(f"Preuzimam API paket {page}...", end=" ", flush=True)
        res, session, aktivni_endpoint_idx = dohvati_api_paket(
            session, page, per_page, cat_id, aktivni_endpoint_idx
        )

        if res is None:
            print("-> Svi API ulazi su blokirani (403). Prekidam radi sigurnosti baze.", flush=True)
            break

        if res.status_code in [400, 404]:
            print("-> Kraj arhive dostignut.", flush=True)
            uspjesno_skenirano = True
            break

        try:
            data = res.json()
        except Exception:
            print("-> Neispravan JSON odgovor.", flush=True)
            break

        if not data:
            print("-> Prazan paket. Kraj arhive.", flush=True)
            uspjesno_skenirano = True
            break

        broj_ploca_u_paketu = 0

        for item in data:
            if not item.get('is_in_stock', False):
                continue

            is_vinyl = False
            s_omot = ""
            s_medij = ""

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
            except Exception:
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
