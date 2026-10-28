import cloudscraper
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
import time
import json
import os
import re

# =========================================================================
# 🎯 OVDJE UNESI URL KATEGORIJE ILI STRANICE ZA IZVIĐANJE
# =========================================================================
CILJANI_URL = "https://lumer-shop.eu/"
# =========================================================================

os.makedirs("logs", exist_ok=True)
LOG_FILE = "logs/scout_report.log"

def log_print(poruka):
    print(poruka)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(poruka + "\n")

def izvidac(url):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write(f"=== SCOUT IZVJEŠTAJ ({time.strftime('%d.%m.%Y %H:%M:%S')}) ===\n")
        
    log_print(f"🚀 POKREĆEM DUBINSKO IZVIĐANJE ZA: {url}")
    log_print("-" * 60)
    
    parsed_url = urlparse(url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    scraper = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "desktop": True})
    
    # 1. API Provjera
    log_print("🔎 [1/4] Provjeravam skrivene API-je...")
    for api_putanja in ["/wp-json/wc/store/v1/products?per_page=5", "/wp-json/wp/v2/product?per_page=5"]:
        try:
            r = scraper.get(urljoin(base_url, api_putanja), timeout=10)
            if r.status_code == 200 and isinstance(r.json(), list) and len(r.json()) > 0:
                log_print(f"  ✅ OTVOREN API: {api_putanja}")
        except Exception:
            pass

    # 2. Analiza glavne stranice (Grid ili Tablica)
    log_print("\n🔎 [2/4] Analiziram strukturu kataloga...")
    try:
        r = scraper.get(url, timeout=20)
        log_print(f"  HTTP Status: {r.status_code}")
        soup = BeautifulSoup(r.text, 'lxml')
        
        tablice = soup.find_all('table')
        if tablice and len(soup.find_all('tr')) > 20:
            log_print("  📊 DETEKTIRANA TABLICA! Provjeravam zaglavlja stupaca:")
            prvi_redovi = soup.find_all('tr')[:5]
            for idx, tr in enumerate(prvi_redovi):
                kolone = [td.get_text(strip=True) for td in tr.find_all(['td', 'th'])]
                if kolone:
                    log_print(f"     Red {idx}: {kolone}")
                    
        # 3. Pametno traženje linka za dubinsku analizu
        proizvodi = soup.select('.product, .type-product, .item, .product-item, .product-layout')
        svi_linkovi = []
        prvi_link = None
        
        if proizvodi:
            log_print(f"  📦 Pronađeno {len(proizvodi)} kartica proizvoda na stranici.")
            for p in proizvodi:
                a_tag = p.find('a', href=True)
                if a_tag:
                    svi_linkovi.append(urljoin(base_url, a_tag['href']))
        else:
            for a in soup.find_all('a', href=True):
                if any(k in a['href'] for k in ['/proizvod/', '/product/', '/artikl/', '/lp-', '/vinyl-']):
                    svi_linkovi.append(urljoin(base_url, a['href']))

        if svi_linkovi:
            # Tražimo link koji sugerira da je riječ o ploči (izbjegavanje majica i opreme)
            kljucne_rijeci = ['vinil', 'vinyl', 'lp', 'ploca', 'ploča', 'record', '7-inch', '12-inch']
            for link in svi_linkovi:
                if any(kljuc in link.lower() for kljuc in kljucne_rijeci):
                    prvi_link = link
                    break
            
            # Ako ni jedan link ne sadrži ključnu riječ, uzimamo prvi dostupni (fallback)
            if not prvi_link:
                prvi_link = svi_linkovi[0]

        log_print("\n🔎 [3/4] Dubinska analiza proizvoda i detekcija stanja (Medij / Omot)...")
        if prvi_link:
            log_print(f"  🔗 Otvaram uzorak proizvoda: {prvi_link}")
            r_prod = scraper.get(prvi_link, timeout=20)
            soup_prod = BeautifulSoup(r_prod.text, 'lxml')
            
            uzorci_stanja = re.compile(r'(stanje|condition|sleeve|omot|ploča|vinil|records|broj stranica|uvez|ocjena|grade)', re.I)
            pronadjena_stanja = []
            
            for el in soup_prod.find_all(['tr', 'p', 'div', 'li', 'span']):
                tekst = el.get_text(" ", strip=True)
                if 3 < len(tekst) < 250 and uzorci_stanja.search(tekst):
                    if re.search(r'\b(M|NM|M-|EX\+|EX|EX-|VG\+|VG|VG-|G\+|G|Novo|New|Sealed)\b', tekst, re.I):
                        klasa = el.get('class', '')
                        parent_klasa = el.parent.get('class', '') if el.parent else ''
                        pronadjena_stanja.append(f"<{el.name} class='{klasa}' (parent: {parent_klasa})> --> TEKST: '{tekst}'")
            
            if pronadjena_stanja:
                log_print("  🎯 PRONAĐENI PODACI O STANJU PLOČE/OMOTA:")
                for ps in list(dict.fromkeys(pronadjena_stanja))[:8]:
                    log_print(f"     ✅ {ps}")
                log_print("  🏷️ ZAKLJUČAK: Trgovina je SECOND HAND ili HIBRIDNA (ocjene su prisutne u gornjim HTML elementima).")
            else:
                log_print("  ℹ️ Nisu pronađene tipične oznake stanja (VG, NM, Condition, Stanje). Trgovina vjerojatno prodaje SAMO NOVE ploče (Mint) ili skriva stanje u slikama.")
        else:
            log_print("  ⚠️ Nije pronađen link na pojedinačni proizvod.")

    except Exception as e:
        log_print(f"  ❌ Greška: {e}")

    log_print("-" * 60)
    log_print("🏁 Izviđanje završeno! Izvještaj spremljen u logs/scout_report.log")

if __name__ == "__main__":
    izvidac(CILJANI_URL)
