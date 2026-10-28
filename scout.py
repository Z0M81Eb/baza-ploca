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

    # 2. Analiza glavne stranice
    log_print("\n🔎 [2/4] Analiziram strukturu kataloga...")
    try:
        r = scraper.get(url, timeout=20)
        log_print(f"  HTTP Status: {r.status_code}")
        soup = BeautifulSoup(r.text, 'lxml')
        
        tablice = soup.find_all('table')
        if tablice and len(soup.find_all('tr')) > 20:
            log_print("  📊 DETEKTIRANA TABLICA! Provjeravam zaglavlja stupaca...")
                    
        # 3. Pametno traženje linka
        proizvodi = soup.select('.product, .type-product, .item, .product-item, .product-layout')
        kandidati = []
        
        if proizvodi:
            log_print(f"  📦 Pronađeno {len(proizvodi)} kartica proizvoda na stranici.")
            for p in proizvodi:
                a_tag = p.find('a', href=True)
                if a_tag:
                    link = urljoin(base_url, a_tag['href'])
                    tekst_kartice = p.get_text(" ", strip=True).lower()
                    kandidati.append((link, tekst_kartice))
        else:
            for a in soup.find_all('a', href=True):
                if any(k in a['href'] for k in ['/proizvod/', '/product/', '/artikl/', '/lp-', '/vinyl-']):
                    link = urljoin(base_url, a['href'])
                    tekst_kartice = a.get_text(" ", strip=True).lower()
                    kandidati.append((link, tekst_kartice))

        prvi_link = None
        kljucne_rijeci = ['vinil', 'vinyl', 'lp', 'ploča', 'ploca', 'record', '7"', '12"', 'cd']
        zabranjene_rijeci = ['majica', 'pederuša', 'torba', 'kapa', 'hoodie', 'čarape']
        
        # 3.1. Tražimo na trenutnoj stranici
        if kandidati:
            for link, tekst in kandidati:
                if any(kljuc in tekst for kljuc in kljucne_rijeci) or any(kljuc in link.lower() for kljuc in kljucne_rijeci):
                    if not any(zab in tekst for zab in zabranjene_rijeci) and not any(zab in link.lower() for zab in zabranjene_rijeci):
                        prvi_link = link
                        log_print(f"  🎯 Detektirana PRAVA ploča na trenutnoj stranici: {prvi_link}")
                        break

        # 3.2. AUTO-DISCOVERY: Ako nema ploča, traži kategoriju u navigaciji (Sitemap / Menu)
        if not prvi_link:
            log_print("  ⚠️ Na trenutnoj stranici nema očitih ploča. Pretražujem navigacijski izbornik za kategoriju ploča...")
            kat_linkovi = []
            for a in soup.find_all('a', href=True):
                href = a['href'].lower()
                tekst = a.get_text(" ", strip=True).lower()
                if any(k in href for k in kljucne_rijeci + ['music', 'glazba']) or any(k in tekst for k in kljucne_rijeci + ['glazba', 'music']):
                    kat_linkovi.append(urljoin(base_url, a['href']))
            
            kat_linkovi = list(dict.fromkeys(kat_linkovi)) # Ukloni duplikate
            
            if kat_linkovi:
                odabrana_kat = kat_linkovi[0]
                log_print(f"  🔗 Pronađena potencijalna kategorija, otvaram: {odabrana_kat}")
                try:
                    r_kat = scraper.get(odabrana_kat, timeout=20)
                    soup_kat = BeautifulSoup(r_kat.text, 'lxml')
                    proizvodi_kat = soup_kat.select('.product, .type-product, .item, .product-item, .product-layout')
                    
                    # OVDJE JE BIO PROBLEM: Sada filtriramo artikle i UNUTAR kategorije
                    kandidati_kat = []
                    for p in proizvodi_kat:
                        a_tag = p.find('a', href=True)
                        if a_tag:
                            kandidati_kat.append((urljoin(base_url, a_tag['href']), p.get_text(" ", strip=True).lower()))
                            
                    for link, tekst in kandidati_kat:
                        if any(kljuc in tekst for kljuc in kljucne_rijeci) or any(kljuc in link.lower() for kljuc in kljucne_rijeci):
                            if not any(zab in tekst for zab in zabranjene_rijeci) and not any(zab in link.lower() for zab in zabranjene_rijeci):
                                prvi_link = link
                                log_print(f"  🎯 Uspješno pronađena PRAVA ploča unutar skrivene kategorije: {prvi_link}")
                                break
                                
                    if not prvi_link and kandidati_kat:
                        prvi_link = kandidati_kat[0][0]
                        log_print(f"  ⚠️ U kategoriji nema očitih ploča po ključnim riječima, uzimam prvi artikl (Fallback): {prvi_link}")
                        
                except Exception as e:
                    log_print(f"  ❌ Greška pri otvaranju kategorije: {e}")

        # 3.3. Fallback ako baš ništa ne radi
        if not prvi_link and kandidati:
            prvi_link = kandidati[0][0]
            log_print("  ⚠️ Nisu prepoznate ključne riječi za ploču nigdje, uzimam prvi proizvod kao test.")

        # 4. Dubinska analiza
        log_print("\n🔎 [3/4] Dubinska analiza proizvoda i detekcija stanja (Medij / Omot)...")
        if prvi_link:
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
                log_print("  🏷️ ZAKLJUČAK: Trgovina je SECOND HAND ili HIBRIDNA (ocjene su prisutne u HTML elementima).")
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
