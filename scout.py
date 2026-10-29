import cloudscraper
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
import time
import os
import re
import sys

# =========================================================================
# 🎯 URL SADA PREUZIMAMO IZ OKOLINE (PROSLIJEĐENO OD WORDPRESSA/GITHUBA)
# =========================================================================
CILJANI_URL = os.environ.get("TARGET_URL", "https://www.cdshop-roxy.com/A.htm").strip()
# =========================================================================

os.makedirs("logs", exist_ok=True)
LOG_FILE = "logs/scout_report.log"

def log_print(poruka):
    print(poruka)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(poruka + "\n")

def izvidac(url):
    if not url:
        print("❌ GREŠKA: Nije zadan TARGET_URL! Skripta se gasi.")
        sys.exit(1)

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

    # 2. Analiza glavne stranice
    log_print("\n🔎 [2/4] Analiziram strukturu kataloga...")
    try:
        r = scraper.get(url, timeout=20)
        log_print(f"  HTTP Status: {r.status_code}")
        soup = BeautifulSoup(r.text, 'lxml')
        
        tablice = soup.find_all('table')
        if tablice and len(soup.find_all('tr')) > 20:
            log_print("  📊 DETEKTIRANA TABLICA S PUNO REDOVA!")
                    
        # 3. Pametno traženje linka za moderne webshopove
        proizvodi = soup.select('.product, .type-product, .item, .product-item, .product-layout')
        kandidati = []
        
        if proizvodi:
            log_print(f"  📦 Pronađeno {len(proizvodi)} modernih kartica proizvoda na stranici.")
            for p in proizvodi:
                a_tag = p.find('a', href=True)
                if a_tag:
                    link = urljoin(base_url, a_tag['href'])
                    kandidati.append((link, p.get_text(" ", strip=True).lower()))
        else:
            for a in soup.find_all('a', href=True):
                href_l = a['href'].lower()
                if any(k in href_l for k in ['/proizvod/', '/product/', '/artikl/', '/lp-', '/vinyl-']):
                    kandidati.append((urljoin(base_url, a['href']), a.get_text(" ", strip=True).lower()))

        prvi_link = None
        kljucne_rijeci = ['vinil', 'vinyl', 'lp', 'ploča', 'ploca', 'record', '7"', '12"']
        zabranjene_rijeci = ['majica', 'pederuša', 'torba', 'kapa', 'hoodie', 'čarape', 'stalci', 'oprema']
        
        if kandidati:
            for link, tekst in kandidati:
                if any(kljuc in tekst for kljuc in kljucne_rijeci) or any(kljuc in link.lower() for kljuc in kljucne_rijeci):
                    if not any(zab in tekst for zab in zabranjene_rijeci) and not any(zab in link.lower() for zab in zabranjene_rijeci):
                        prvi_link = link
                        log_print(f"  🎯 Detektirana ploča po linku: {prvi_link}")
                        break

        # AUTO-DISCOVERY Meni (ako nema proizvoda na naslovnici)
        if not prvi_link and not tablice:
            log_print("  ⚠️ Na trenutnoj stranici nema ploča, a ni tablice. Tražim kategorije...")
            kat_linkovi = []
            for a in soup.find_all('a', href=True):
                href = a['href'].lower()
                tekst = a.get_text(" ", strip=True).lower()
                if any(k in href for k in kljucne_rijeci + ['music', 'glazba']) or any(k in tekst for k in kljucne_rijeci + ['glazba', 'music']):
                    kat_linkovi.append(urljoin(base_url, a['href']))
            
            if kat_linkovi:
                odabrana_kat = kat_linkovi[0]
                log_print(f"  🔗 Otvaram pronađenu kategoriju: {odabrana_kat}")
                try:
                    r_kat = scraper.get(odabrana_kat, timeout=20)
                    soup_kat = BeautifulSoup(r_kat.text, 'lxml')
                    
                    for p in soup_kat.select('.product, .type-product, .item'):
                        a_tag = p.find('a', href=True)
                        if a_tag:
                            prvi_link = urljoin(base_url, a_tag['href'])
                            log_print(f"  🎯 Uspješno pronađena ploča u kategoriji: {prvi_link}")
                            break
                except Exception as e:
                    log_print(f"  ❌ Greška pri otvaranju kategorije: {e}")

        # 4. Dubinska analiza (MODERNO ILI DINOSAUR)
        log_print("\n🔎 [3/4] Dubinska analiza proizvoda i detekcija stanja (Medij / Omot)...")
        
        uzorci_stanja = re.compile(r'(stanje|condition|sleeve|omot|ploča|vinil|records|ocjena|grade)', re.I)
        ocjene_regex = re.compile(r'\b(M|NM|M-|EX\+|EX|EX-|VG\+|VG|VG-|G\+|G|Novo|New|Sealed)\b', re.I)
        pronadjena_stanja = []
        
        if prvi_link:
            r_prod = scraper.get(prvi_link, timeout=20)
            soup_prod = BeautifulSoup(r_prod.text, 'lxml')
            elementi_za_analizu = soup_prod.find_all(['tr', 'p', 'div', 'li', 'span'])
            
        elif tablice and len(soup.find_all('tr')) > 20:
            log_print("  🦕 DINOSAUR MODO AKTIVAN: Nema linkova na proizvode, ali postoji ogromna tablica. Analiziram direktno retke!")
            elementi_za_analizu = soup.find_all('tr')
            
        else:
            elementi_za_analizu = []
            log_print("  ⚠️ Nije pronađen link na proizvod niti validna tablica za skeniranje.")

        for el in elementi_za_analizu:
            tekst = el.get_text(" ", strip=True)
            # Tražimo tekst optimalne dužine u kojem se spominju standardne ocjene
            if 3 < len(tekst) < 400:
                if uzorci_stanja.search(tekst) or (not prvi_link and ocjene_regex.search(tekst)):
                    if ocjene_regex.search(tekst):
                        klasa = el.get('class', 'bez_klase')
                        pronadjena_stanja.append(f"<{el.name} class='{klasa}'> --> {tekst}")
        
        if pronadjena_stanja:
            log_print("  🎯 PRONAĐENI PODACI O STANJU PLOČE/OMOTA:")
            for ps in list(dict.fromkeys(pronadjena_stanja))[:8]:
                log_print(f"     ✅ {ps}")
            log_print("  🏷️ ZAKLJUČAK: Trgovina je SECOND HAND ili HIBRIDNA (ocjene prisutne).")
        else:
            log_print("  ℹ️ Nisu pronađene tipične oznake stanja. Trgovina prodaje NOVE ploče ili skriva stanje u slikama.")

    except Exception as e:
        log_print(f"  ❌ Greška izviđača: {e}")

    log_print("-" * 60)
    log_print("🏁 Izviđanje završeno! Izvještaj spremljen u logs/scout_report.log")

if __name__ == "__main__":
    izvidac(CILJANI_URL)
