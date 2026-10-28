import cloudscraper
from bs4 import BeautifulSoup
import csv
import os
import re
import time
import html
from urllib.parse import urljoin

SITE_URL = "https://lumer-shop.eu"
CSV_FILENAME = "lumer_ploce.csv"

# ==========================================
# FILTERI ZA PREPOZNAVANJE PLOČA I ODBACIVANJE MERCHA
# ==========================================
KLJUCNE_RIJECI = ['vinil', 'vinyl', 'lp', 'ploča', 'ploca', 'record', '7"', '12"']
ZABRANJENE_RIJECI = ['majica', 'pederuša', 'torba', 'kapa', 'hoodie', 'čarape', 'cd', 'kazeta', 'kaset', 'dvd', 'karta', 'ulaznica', 'beanie', 'dukserica']

def je_li_ploca(naslov, url, kategorije=""):
    tekst = f"{naslov} {url} {kategorije}".lower()
    
    # 1. Prvo eliminiramo zabranjene riječi (merch, cd, oprema)
    if any(zabranjeno in tekst for zabranjeno in ZABRANJENE_RIJECI):
        return False
        
    # 2. Zatim tražimo potvrdu da je riječ o ploči
    if any(kljuc in tekst for kljuc in KLJUCNE_RIJECI):
        return True
        
    return False

def ocisti_cijenu(tekst):
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
            except:
                pass
    return "0.00"

def normaliziraj_link(href):
    if not href:
        return ""
    puni = urljoin(SITE_URL, href.strip())
    return puni.split('?')[0].rstrip('/') + '/'

def scrape_lumer():
    print("🚀 POKREĆEM LUMER SHOP SCRAPER (API + HTML Auto-Discovery)...", flush=True)
    
    sve_ploce = {}
    vidjeni_linkovi = set()
    uspjesno_skenirano = False

    # 1. Učitavanje postojećeg CSV-a radi brisanja rasprodanih artikala
    if os.path.exists(CSV_FILENAME):
        with open(CSV_FILENAME, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) >= 7:
                    norm_k = normaliziraj_link(row[2])
                    row[2] = norm_k
                    sve_ploce[norm_k] = row
        print(f"📦 Učitano {len(sve_ploce)} postojećih ploča.", flush=True)

    scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})
    
    api_radi = False
    page = 1
    
    print("\n--- 📡 Pokušavam brzi WooCommerce Store API ---", flush=True)
    while True:
        api_url = f"{SITE_URL}/wp-json/wc/store/products?page={page}&per_page=100"
        try:
            res = scraper.get(api_url, timeout=30)
            if res.status_code != 200 or not res.text.strip().startswith('['):
                break
            
            data = res.json()
            if not data:
                uspjesno_skenirano = True
                break
                
            api_radi = True
            dodano = 0
            
            for item in data:
                if not item.get('is_in_stock', False):
                    continue
                    
                title = html.unescape(item.get('name', '')).strip()
                link = normaliziraj_link(item.get('permalink', ''))
                kategorije = " ".join([k.get('name', '') for k in item.get('categories', [])])
                
                # Primjena filtera!
                if not je_li_ploca(title, link, kategorije):
                    continue
                
                try:
                    prices = item.get('prices', {})
                    price = f"{float(prices.get('price', '0')) / (10 ** prices.get('currency_minor_unit', 2)):.2f}"
                except:
                    price = "0.00"
                    
                if price == "0.00":
                    continue
                    
                images = item.get('images', [])
                img_url = images[0].get('src', '') if images else ""
                
                # Stanje je uvijek "Novo" kako je izviđač potvrdio
                stanje_medija = "Novo"
                stanje_omota = "Novo"
                tip_artikla = 'Singl 7"' if '7"' in title.lower() or 'ep' in title.lower() else "LP"
                
                sve_ploce[link] = [title, price, link, img_url, stanje_medija, stanje_omota, tip_artikla]
                vidjeni_linkovi.add(link)
                dodano += 1
                
            print(f"API Stranica {page} -> Preuzeto ploča: {dodano}", flush=True)
            page += 1
            time.sleep(0.5)
            
        except Exception as e:
            print(f"⚠️ Greška na API-ju: {e}", flush=True)
            break

    # 2. FALLBACK NA HTML AKO API NE RADI
    if not api_radi:
        print("\n--- 🌐 Pokrećem HTML skeniranje kategorija ---", flush=True)
        # Skeniramo obje lokacije koje smo otkrili
        kategorije_za_skeniranje = [
            f"{SITE_URL}/product-category/vinyl-cd/",
            f"{SITE_URL}/product-category/misc/music/"
        ]
        
        for base_cat_url in kategorije_za_skeniranje:
            print(f"\n📂 Skeniram kategoriju: {base_cat_url}")
            current_page_num = 1
            
            while True:
                cat_url = base_cat_url if current_page_num == 1 else f"{base_cat_url}page/{current_page_num}/"
                try:
                    response = scraper.get(cat_url, timeout=30)
                    if response.status_code == 404:
                        uspjesno_skenirano = True
                        break
                    if response.status_code != 200:
                        break
                        
                    soup = BeautifulSoup(response.text, 'lxml')
                    products = soup.select('.product, .type-product, .item')
                    
                    if not products:
                        uspjesno_skenirano = True
                        break
                        
                    dodano = 0
                    for prod in products:
                        klase = " ".join(prod.get('class', [])).lower()
                        if 'outofstock' in klase:
                            continue
                            
                        a_tag = prod.find('a', href=True)
                        if not a_tag: continue
                        
                        link = normaliziraj_link(a_tag['href'])
                        title_el = prod.select_one('.woocommerce-loop-product__title, h2, h3')
                        title = html.unescape(title_el.get_text(" ", strip=True)).strip() if title_el else ""
                        
                        # Primjena filtera!
                        if not je_li_ploca(title, link):
                            continue
                            
                        bdi = prod.select_one('.price ins bdi, .price bdi, .price')
                        price = ocisti_cijenu(bdi.get_text(" ", strip=True) if bdi else prod.get_text(" ", strip=True))
                        
                        if not title or price == "0.00":
                            continue
                            
                        img_tag = prod.select_one('img')
                        img_url = img_tag.get('src', '') if img_tag else ""
                        
                        stanje_medija = "Novo"
                        stanje_omota = "Novo"
                        tip_artikla = 'Singl 7"' if '7"' in title.lower() or 'ep' in title.lower() else "LP"
                        
                        sve_ploce[link] = [title, price, link, img_url, stanje_medija, stanje_omota, tip_artikla]
                        vidjeni_linkovi.add(link)
                        dodano += 1
                        
                    print(f"  -> HTML Stranica {current_page_num} -> Preuzeto ploča: {dodano}", flush=True)
                    current_page_num += 1
                    time.sleep(1)
                    
                except Exception as e:
                    print(f"⚠️ HTML Greška: {e}")
                    break

    # 3. SIGURNO BRISANJE I SPREMANJE
    if uspjesno_skenirano and len(vidjeni_linkovi) > 0:
        pocetni = len(sve_ploce)
        sve_ploce = {k: v for k, v in sve_ploce.items() if k in vidjeni_linkovi}
        print(f"\n🧹 Sinkronizacija prodanih ploča uspješna (Obrisano: {pocetni - len(sve_ploce)} starih).", flush=True)
    elif uspjesno_skenirano and len(vidjeni_linkovi) == 0:
        print("\n⚠️ Oprez: Pretraga je završila, ali nije pronađena niti jedna ploča. Postojeći CSV ostaje netaknut.", flush=True)

    if sve_ploce:
        with open(CSV_FILENAME, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Naslov', 'Cijena', 'URL_Proizvoda', 'URL_Slike', 'Stanje_Medija', 'Stanje_Omota', 'Tip_Artikla'])
            writer.writerows(sve_ploce.values())
        print(f"🎉 GOTOVO! Ukupno u CSV-u: {len(sve_ploce)} Lumer ploča.", flush=True)

if __name__ == "__main__":
    scrape_lumer()
