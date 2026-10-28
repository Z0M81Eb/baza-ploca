import cloudscraper
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
import time
import json

# =========================================================================
# 🎯 OVDJE UNESI URL KATEGORIJE ILI STRANICE ZA IZVIĐANJE
# =========================================================================
CILJANI_URL = "https://antikvarijatstaklenigrad.hr/gramofonske-ploce/"
# =========================================================================

def izvidac(url):
    print(f"🚀 POKREĆEM IZVIĐANJE ZA: {url}")
    print("-" * 50)
    
    parsed_url = urlparse(url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    
    scraper = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "desktop": True})
    
    # 1. Provjera skrivenih API-ja (WooCommerce / WP REST)
    print("🔎 [1/3] Provjeravam skrivene API-je i baze...")
    api_kandidati = [
        "/wp-json/wc/store/v1/products?per_page=5",
        "/wp-json/wp/v2/product?per_page=5"
    ]
    
    api_otvoren = False
    for api_putanja in api_kandidati:
        test_url = urljoin(base_url, api_putanja)
        try:
            r = scraper.get(test_url, timeout=15)
            if r.status_code == 200:
                try:
                    data = r.json()
                    if isinstance(data, list) and len(data) > 0:
                        print(f"  ✅ OTVOREN API PRONAĐEN: {api_putanja}")
                        print(f"     Prvi artikl u API-ju: {data[0].get('name', data[0].get('title', {}).get('rendered', 'Nepoznato'))}")
                        api_otvoren = True
                except json.JSONDecodeError:
                    pass
        except Exception as e:
            print(f"  ⚠️ Greška pri provjeri API-ja {api_putanja}: {e}")
            
    if not api_otvoren:
        print("  ❌ Nema otvorenih javnih WooCommerce API-ja. Morat ćemo parsirati HTML.")

    # 2. Analiza HTML-a i Cloudflare zaštite
    print("\n🔎 [2/3] Analiziram HTML i poslužitelj...")
    try:
        t_start = time.time()
        r = scraper.get(url, timeout=20)
        t_end = time.time()
        
        print(f"  HTTP Status: {r.status_code} (Odgovor u {t_end - t_start:.2f}s)")
        
        server_header = r.headers.get('Server', '').lower()
        if 'cloudflare' in server_header:
            print("  🛡️ Detektiran Cloudflare sustav zaštite.")
        
        soup = BeautifulSoup(r.text, 'lxml')
        
        # Detekcija platforme
        is_wp = soup.find('link', href=lambda x: x and 'wp-content' in x)
        is_woo = soup.find('body', class_=lambda x: x and 'woocommerce' in x)
        
        if is_woo:
            print("  🛒 CMS Platforma: WordPress + WooCommerce")
        elif is_wp:
            print("  📝 CMS Platforma: WordPress")
        else:
            print("  ❓ CMS Platforma: Nepoznata / Custom")
            
        # 3. Paginacija i struktura kartica
        print("\n🔎 [3/3] Tražim paginaciju i proizvode...")
        
        paginacija = soup.find_all('a', class_=lambda x: x and 'page-numbers' in x)
        if not paginacija:
            paginacija = soup.find_all('a', href=lambda x: x and ('page=' in x or '/page/' in x or 'p=' in x))
            
        if paginacija:
            linkovi = set([a['href'] for a in paginacija if a.has_attr('href')])
            print(f"  📄 Pronađena paginacija! Uzorci linkova: {list(linkovi)[:2]}")
        else:
            print("  ⚠️ Nije pronađena klasična paginacija (Možda je Infinite Scroll ili mala kategorija).")
            
        # Analiza proizvoda (klasične WooCommerce klase)
        proizvodi = soup.select('.product, .type-product, .wc-block-grid__product')
        if proizvodi:
            print(f"  📦 Pronađeno {len(proizvodi)} proizvoda na stranici.")
            prvi = proizvodi[0]
            naslov = prvi.find(['h2', 'h3'], class_=lambda x: x and 'title' in x)
            cijena = prvi.select_one('.price')
            print("  --- Uzorak prvog pronađenog HTML proizvoda ---")
            print(f"      Naslov: {naslov.get_text(strip=True) if naslov else 'Nije nađeno'}")
            print(f"      Cijena: {cijena.get_text(strip=True) if cijena else 'Nije nađeno'}")
        else:
            print("  ❌ Nisu pronađeni elementi s klasičnim CSS klasama proizvoda.")
            
    except Exception as e:
        print(f"  ❌ Kritična greška pri dohvaćanju HTML-a: {e}")
        
    print("-" * 50)
    print("🏁 Izviđanje završeno!")

if __name__ == "__main__":
    izvidac(CILJANI_URL)
