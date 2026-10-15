from curl_cffi import requests as cffi_requests
import csv
import os
import time
import html

# --- POSTAVKE ---
SITE_URL = "https://menartshop.hr"
CSV_FILENAME = "menart_katalog.csv"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "hr-HR,hr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://menartshop.hr/",
    "Origin": "https://menartshop.hr"
}

# Lista mogućih API ulaza (ako firewall blokira /wp-json/, skripta automatski prelazi na ?rest_route=)
API_ENDPOINTS = [
    ("https://menartshop.hr/wp-json/wc/store/products", "?"),
    ("https://menartshop.hr/wp-json/wc/store/v1/products", "?"),
    ("https://menartshop.hr/?rest_route=/wc/store/products", "&"),
    ("https://menartshop.hr/?rest_route=/wc/store/v1/products", "&")
]

CAT_ENDPOINTS = [
    "https://menartshop.hr/wp-json/wc/store/products/categories?per_page=100",
    "https://menartshop.hr/?rest_route=/wc/store/products/categories&per_page=100"
]

def kreiraj_sesiju(profil="chrome120"):
    session = cffi_requests.Session(impersonate=profil)
    session.headers.update(HEADERS)
    try:
        # Inicijalni posjet naslovnici radi preuzimanja kolačića (cookies)
        session.get(SITE_URL, timeout=20)
        time.sleep(1)
    except Exception as e:
        print(f"⚠️ Upozorenje pri otvaranju naslovnice: {e}", flush=True)
    return session

def get_glazba_category_id(session):
    print("🔍 Tražim ID kategorije 'Glazba'...", flush=True)
    for url in CAT_ENDPOINTS:
        try:
            res = session.get(url, timeout=20)
            if res.status_code == 200:
                for cat in res.json():
                    if cat.get('slug') == 'glazba':
                        print(f"✅ Kategorija 'Glazba' pronađena (ID: {cat['id']})", flush=True)
                        return cat['id']
        except Exception:
            continue

    print("⚠️ Nastavljam bez filtera kategorije (skenirat ću sve i filtrirati ručno).", flush=True)
    return None

def dohvati_api_paket(session, page, per_page, glazba_id, aktivni_endpoint_idx):
    """Pokušava dohvatiti paket preko aktivnog endpointa, a u slučaju 403 isprobava rezervne ulaze i profile."""
    profili = ["chrome120", "chrome116", "safari15_5"]

    for idx in range(aktivni_endpoint_idx, len(API_ENDPOINTS)):
        base_url, sep = API_ENDPOINTS[idx]
        if glazba_id:
            url = f"{base_url}{sep}category={glazba_id}&page={page}&per_page={per_page}"
        else:
            url = f"{base_url}{sep}page={page}&per_page={per_page}"

        for pokusaj, profil in enumerate(profili):
            try:
                if pokusaj > 0:
                    session = kreiraj_sesiju(profil)
                res = session.get(url, timeout=25)
                if res.status_code in [200, 400, 404]:
                    return res, session, idx
                print(f"[Kod: {res.status_code} na ulazu {idx+1}]", end=" ", flush=True)
                time.sleep(2)
            except Exception as e:
                print(f"[Greška: {e}]", end=" ", flush=True)
                time.sleep(2)

    return None, session, aktivni_endpoint_idx

def scrape_menart_api():
    sve_ploce = {}
    vidjeni_linkovi = set()
    uspjesno_skenirano = False

    # Učitavanje postojećeg CSV-a radi zaštite od brisanja u slučaju pada mreže
    if os.path.exists(CSV_FILENAME):
        with open(CSV_FILENAME, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) >= 7:
                    sve_ploce[row[2]] = row
        print(f"📦 Učitano {len(sve_ploce)} postojećih ploča iz memorije.", flush=True)

    session = kreiraj_sesiju("chrome120")
    glazba_id = get_glazba_category_id(session)

    page = 1
    per_page = 80
    aktivni_endpoint_idx = 0

    print("\n🚀 Pokrećem brzi Chrome-TLS API sweep za Menart...", flush=True)

    while True:
        print(f"📄 Preuzimam API paket {page}...", end=" ", flush=True)
        res, session, aktivni_endpoint_idx = dohvati_api_paket(
            session, page, per_page, glazba_id, aktivni_endpoint_idx
        )

        if res is None:
            print("-> ❌ Svi API ulazi su odbili pristup (403/Mreža). Prekidam radi sigurnosti baze.", flush=True)
            break

        if res.status_code in [400, 404]:
            print("-> Kraj baze dosegnut.", flush=True)
            uspjesno_skenirano = True
            break

        try:
            data = res.json()
        except Exception:
            print("-> Neispravan JSON odgovor. Prekidam.", flush=True)
            break

        if not data:
            print("-> Paket prazan, završavam.", flush=True)
            uspjesno_skenirano = True
            break

        dodano = 0

        for item in data:
            if not item.get('is_in_stock', False):
                continue

            naslov = html.unescape(item.get('name', 'Nepoznat naslov')).strip()
            attributes = item.get('attributes', [])
            format_ploca = ""
            je_li_vinil = False

            for attr in attributes:
                attr_name = attr.get('name', '').lower()
                if 'format' in attr_name:
                    terms = attr.get('terms', [])
                    if terms:
                        format_val = terms[0].get('name', '')
                        if any(k in format_val.lower() for k in ['lp', 'vinyl', 'vinil', 'single']):
                            format_ploca = format_val
                            je_li_vinil = True

            if not je_li_vinil:
                naslov_lower = f" {naslov.lower()} "
                if any(k in naslov_lower for k in [' lp ', '2lp', 'vinyl', 'vinil']):
                    je_li_vinil = True
                    if not format_ploca:
                        format_ploca = "LP"

            if not je_li_vinil:
                continue

            if not format_ploca:
                format_ploca = "LP"

            link = item.get('permalink', '')
            prices = item.get('prices', {})
            raw_price = prices.get('price', '0')
            minor_unit = prices.get('currency_minor_unit', 2)
            try:
                price_val = float(raw_price) / (10 ** minor_unit)
                cijena = f"{price_val:.2f}"
            except Exception:
                cijena = "0.00"

            if cijena == "0.00" or not link:
                continue

            images = item.get('images', [])
            slika_url = images[0].get('src', '') if images else ""

            sve_ploce[link] = [naslov, cijena, link, slika_url, "Novo", "Novo", format_ploca]
            vidjeni_linkovi.add(link)
            dodano += 1

        print(f"-> Pronađeno {dodano} aktivnih vinila u paketu.", flush=True)
        page += 1
        time.sleep(0.6)

    # Čišćenje prodanih samo ako je skeniranje prošlo do kraja
    if uspjesno_skenirano and len(vidjeni_linkovi) > 50:
        pocetni_broj = len(sve_ploce)
        sve_ploce = {k: v for k, v in sve_ploce.items() if k in vidjeni_linkovi}
        obrisano = pocetni_broj - len(sve_ploce)
        print(f"\n🧹 Analiza završena. Obrisano {obrisano} prodanih ploča.", flush=True)
    else:
        print("\n⚠️ UPOZORENJE: Skeniranje nije došlo do kraja. Zadržavam postojeće ploče u bazi.", flush=True)

    if sve_ploce:
        zaglavlja = ["Naslov", "Cijena", "URL_Proizvoda", "URL_Slike", "Stanje_Medija", "Stanje_Omota", "Tip_Artikla"]
        with open(CSV_FILENAME, mode='w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(zaglavlja)
            writer.writerows(sve_ploce.values())
        print(f"🎉 GOTOVO! U bazi je ukupno {len(sve_ploce)} ploča.", flush=True)

if __name__ == "__main__":
    scrape_menart_api()
