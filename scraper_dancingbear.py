import requests
import csv
import os
import time
import html

print(f"Trenutna radna mapa: {os.getcwd()}", flush=True)
print("DANCING BEAR: Pokrećem napredni API Sweep s 'Mirror' logikom...", flush=True)

csv_filename = 'dancingbear_ploce.csv'
sve_ploce = {}
vidjeni_linkovi = set()
uspjesno_skenirano = False

# === 1. UČITAVANJE STARE BAZE (MIRROR LOGIKA) ===
# Ne krećemo s praznom listom! Čuvamo stare podatke dok nismo 100% sigurni.
if os.path.exists(csv_filename):
    with open(csv_filename, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if len(row) >= 7:
                sve_ploce[row[2]] = row
    print(f"Učitano {len(sve_ploce)} postojećih ploča iz memorije.", flush=True)

page = 1
per_page = 100 
max_retries = 3 # Zaštita od padanja servera

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0'
})

print("\n=== POKREĆEM SKENIRANJE ===", flush=True)

while True:
    api_url = f"https://dancingbear.hr/wp-json/wc/store/products?page={page}&per_page={per_page}"
    print(f"Preuzimam API paket {page}...", end=" ", flush=True)
    
    pokusaj = 0
    uspjeh_paket = False
    data = None
    
    # Retry petlja (ako server vrati grešku, čeka 5 sekundi pa proba ponovno)
    while pokusaj < max_retries:
        try:
            res = session.get(api_url, timeout=30)
            
            if res.status_code == 400:
                print("-> Kraj arhive dosegnut.", flush=True)
                uspjesno_skenirano = True
                uspjeh_paket = True
                break
                
            elif res.status_code != 200:
                print(f" [GREŠKA: {res.status_code}] Ponovni pokušaj...", end=" ", flush=True)
                pokusaj += 1
                time.sleep(5)
                continue
                
            data = res.json()
            if not data:
                print("-> Paket prazan. Kraj arhive.", flush=True)
                uspjesno_skenirano = True
                uspjeh_paket = True
                break
                
            uspjeh_paket = True
            break # Uspješno preuzeto, izlazimo iz retry petlje
            
        except Exception as e:
            print(f" [GREŠKA MREŽE] Ponovni pokušaj...", end=" ", flush=True)
            pokusaj += 1
            time.sleep(5)
            
    # Ako je nakon 3 pokušaja i dalje greška, prekidamo sigurno (bez brisanja baze)
    if not uspjeh_paket or res.status_code == 400 or not data:
        if not uspjesno_skenirano:
            print(f"\nUPOZORENJE: Prekid na stranici {page}. Preskačem brisanje starih ploča radi sigurnosti.", flush=True)
        break
        
    dodano_na_stranici = 0
    
    for item in data:
        if not item.get('is_in_stock', False):
            continue
            
        title = html.unescape(item.get('name', 'Nepoznat naslov')).strip()
        title_lower = title.lower()
        kategorije = [k.get('slug', '').lower() for k in item.get('categories', [])]
        
        # --- PAMETNI FILTER ZA VINILE ---
        dopustene_rijeci = ['vinyl', 'vinil', 'ploce', 'ploca', 'lp', '7-inch', '12-inch']
        is_vinyl = False
        
        if any(rijec in k for k in kategorije for rijec in dopustene_rijeci):
            is_vinyl = True
        # Spašava artikle kojima je kategorija kriva, ali u naslovu piše LP (kao Alice Cooper LP2)
        elif ' lp' in title_lower or '-lp' in title_lower or 'lp2' in title_lower or 'vinyl' in title_lower:
            is_vinyl = True
            
        if not is_vinyl:
            continue
            
        link = item.get('permalink', '')
        vidjeni_linkovi.add(link)
        
        prices = item.get('prices', {})
        raw_price = prices.get('price', '0')
        minor_unit = prices.get('currency_minor_unit', 2)
        try:
            price_val = float(raw_price) / (10 ** minor_unit)
            price = f"{price_val:.2f}"
        except:
            price = "0.00"
            
        if price == "0.00":
            continue
            
        images = item.get('images', [])
        img_url = images[0].get('src', '') if images and isinstance(images, list) else ""
        
        stanje = "Novo"
        tip = "Vinil"
        
        # Ažuriramo cijenu/sliku ili dodajemo novu ploču
        sve_ploce[link] = [title, price, link, img_url, stanje, stanje, tip]
        dodano_na_stranici += 1
        
    print(f"-> Pronađeno vinila: {dodano_na_stranici}", flush=True)
    page += 1
    time.sleep(0.5)

# === 3. LOGIKA BRISANJA I SPREMANJE ===
if uspjesno_skenirano:
    pocetni_broj = len(sve_ploce)
    # Brišemo samo one ploče koje danas NISMO vidjeli
    sve_ploce = {k: v for k, v in sve_ploce.items() if k in vidjeni_linkovi}
    obrisano = pocetni_broj - len(sve_ploce)
    print(f"\nAnaliza završena. Obrisano {obrisano} prodanih ploča koje više ne postoje na webshopu.", flush=True)

with open(csv_filename, 'w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow(['Naslov', 'Cijena', 'URL_Proizvoda', 'URL_Slike', 'Stanje_Medija', 'Stanje_Omota', 'Tip_Artikla'])
    writer.writerows(sve_ploce.values())

print(f"Završeno! Baza je ažurna i sadrži {len(sve_ploce)} ploča.", flush=True)
