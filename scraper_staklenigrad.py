import cloudscraper
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import time
import re
import urllib3
import html

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SITE_URL = "https://antikvarijatstaklenigrad.hr/gramofonske-ploce/"
CSV_FILENAME = "scraper_staklenigrad.csv"
BROJ_NITI = 5 # Cloudflare je osjetljiv, držimo se 5 niti

def create_scraper():
    scraper = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "desktop": True})
    scraper.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://antikvarijatstaklenigrad.hr/"
    })
    return scraper

def ocisti_url_slike(raw_url):
    if not raw_url:
        return ""
    val = html.unescape(str(raw_url)).strip()
    val = re.sub(r'-\d+x\d+\.(jpg|jpeg|png|webp|gif)$', r'.\1', val, flags=re.IGNORECASE)
    return val

def izvuci_cijenu(price_el):
    if not price_el:
        return "0.00"
    
    ins = price_el.find('ins')
    if ins:
        tekst = ins.get_text("", strip=True)
    else:
        tekst = price_el.get_text("", strip=True)
        
    tekst_spojeno = tekst.replace(".", "")
    parovi = re.findall(r'(\d{1,4}),(\d{2})', tekst_spojeno)
    
    if parovi:
        try:
            val = float(f"{parovi[-1][0]}.{parovi[-1][1]}")
            if val > 0:
                return f"{val:.2f}"
        except Exception:
            pass
    return "0.00"

def izvuci_detaljno_stanje(product_url, scraper):
    """
    Ulazi na stranicu pojedinačne ploče kako bi pronašao ocjene.
    Stakleni Grad drži stanje medija pod atributom "Broj stranica",
    a stanje omota pod atributom "Uvez".
    """
    stanje_medija = ""
    stanje_omota = ""
    
    for _ in range(2): # Maksimalno 2 pokušaja po ploči
        try:
            res = scraper.get(product_url, timeout=15)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "lxml")
                
                # Lovimo atribut "Broj stranica" za Medij
                red_medij = soup.find("tr", class_=lambda x: x and "attribute_broj-stranica" in x)
                if red_medij:
                    val_el = red_medij.find("td", class_="woocommerce-product-attributes-item__value")
                    if val_el:
                        stanje_medija = val_el.get_text(" ", strip=True)
                
                # Lovimo atribut "Uvez" za Omot
                red_omot = soup.find("tr", class_=lambda x: x and "attribute_uvez" in x)
                if red_omot:
                    val_el = red_omot.find("td", class_="woocommerce-product-attributes-item__value")
                    if val_el:
                        stanje_omota = val_el.get_text(" ", strip=True)
                
                # Ako slučajno nisu upisali, default je VG
                if not stanje_medija: stanje_medija = "VG"
                if not stanje_omota: stanje_omota = "VG"
                
                return stanje_medija, stanje_omota
                
        except Exception:
            time.sleep(1)
            
    return "VG", "VG" # Fallback ako Cloudflare odbije

def obradi_stranicu(page):
    url = SITE_URL if page == 1 else f"{SITE_URL}page/{page}/"
    scraper = create_scraper()
    stavke = []
    
    for pokusaj in range(3):
        try:
            res = scraper.get(url, timeout=20)
            if res.status_code == 404:
                return page, True, [] 
                
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "lxml")
                kartice = soup.select(".product, .type-product")
                
                for k in kartice:
                    a_tag = k.find("a", class_="woocommerce-LoopProduct-link") or k.find("a")
                    if not a_tag or not a_tag.get("href"):
                        continue
                        
                    link = a_tag["href"]
                    
                    title_tag = k.find(["h2", "h3", "h1"])
                    naslov = title_tag.get_text(strip=True) if title_tag else ""
                    if not naslov:
                        naslov_img = k.find("img")
                        if naslov_img and naslov_img.get("alt"):
                            naslov = naslov_img.get("alt")
                    
                    if not naslov:
                        continue
                        
                    price_tag = k.select_one(".price")
                    cijena = izvuci_cijenu(price_tag)
                    if cijena == "0.00":
                        continue
                        
                    img_tag = k.find("img")
                    slika_url = ""
                    if img_tag:
                        slika_url = img_tag.get("data-src") or img_tag.get("data-lazy-src") or img_tag.get("src") or ""
                        slika_url = ocisti_url_slike(slika_url)
                        
                    # Za svaku pronađenu ploču sada idemo izvući konkretne ocjene!
                    medij, omot = izvuci_detaljno_stanje(link, scraper)
                        
                    stavke.append([naslov, cijena, link, slika_url, medij, omot, "LP"])
                    
                return page, True, stavke
                
        except Exception:
            time.sleep(2.5 * (pokusaj + 1))
            
    return page, False, []

def scrape_stakleni_grad():
    t_start = time.time()
    print("⚡ Zagrijavam konekciju prema Antikvarijat Stakleni Grad...")
    
    scraper = create_scraper()
    res1 = scraper.get(SITE_URL, timeout=20)
    
    if res1.status_code != 200:
        print(f"❌ Neuspješno spajanje (HTTP {res1.status_code}). Prekidam.")
        return
        
    soup1 = BeautifulSoup(res1.text, "lxml")
    
    max_pages = 1
    page_numbers = soup1.select(".page-numbers:not(.next):not(.prev)")
    if page_numbers:
        try:
            max_pages = max([int(p.get_text(strip=True)) for p in page_numbers if p.get_text(strip=True).isdigit()])
        except Exception:
            pass
            
    print(f"📄 Pronađeno {max_pages} stranica. Pokrećem detaljno preuzimanje i analizu ocjena (Ovo će potrajati)...")
    
    sve_ploce = {}
    
    # Korištenje Executor pool-a za paralelno kopanje po stranicama
    with ThreadPoolExecutor(max_workers=BROJ_NITI) as executor:
        zadaci = [executor.submit(obradi_stranicu, p) for p in range(1, max_pages + 1)]
        
        for future in as_completed(zadaci):
            page, ok, stavke = future.result()
            if ok:
                for s in stavke:
                    sve_ploce[s[2]] = s 
                print(f"✅ Stranica {page} obrađena (Detaljno analizirano {len(stavke)} ploča)")
            else:
                print(f"❌ Greška na stranici {page} - preskočena nakon 3 pokušaja.")
                
    if sve_ploce:
        with open(CSV_FILENAME, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Naslov', 'Cijena', 'URL_Proizvoda', 'URL_Slike', 'Stanje_Medija', 'Stanje_Omota', 'Tip_Artikla'])
            writer.writerows(sve_ploce.values())
            
        ukupno_min = (time.time() - t_start) / 60.0
        print(f"\n🎉 GOTOVO za {ukupno_min:.1f} min! Uspješno preuzeto i ocijenjeno {len(sve_ploce)} ploča u datoteku {CSV_FILENAME}.")
    else:
        print("\n⚠️ Skripta nije pronašla niti jednu ploču.")

if __name__ == "__main__":
    scrape_stakleni_grad()
