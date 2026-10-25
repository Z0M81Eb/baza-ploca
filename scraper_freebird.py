import cloudscraper
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

URL_LP = "https://freebird.hr/index.php?route=product/category&path=225_223"

def provjeri_html_kartice():
    s = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "desktop": True})
    print(f"🔎 Otvaram LP kategoriju: {URL_LP}", flush=True)
    r = s.get(URL_LP, timeout=30)
    print(f"✅ Status: {r.status_code} | Duljina: {len(r.text)}", flush=True)

    soup = BeautifulSoup(r.text, "lxml")

    # 1. Provjera možemo li povećati limit po stranici (npr. &limit=100) da paginacija bude 5x brža
    r_limit = s.get(URL_LP + "&limit=100", timeout=30)
    soup_limit = BeautifulSoup(r_limit.text, "lxml")
    kartice_100 = soup_limit.select(".product-layout, .product-thumb")
    print(f"⚡ Test '&limit=100': pronađeno {len(kartice_100)} proizvoda na jednoj stranici!", flush=True)

    # 2. Ispis prve kartice proizvoda
    kartice = soup.select(".product-layout, .product-thumb")
    print(f"\n📦 Pronađeno kartica na standardnoj stranici: {len(kartice)}", flush=True)
    if kartice:
        print("=== HTML PRVE KARTICE ===")
        print(kartice[0].prettify(), flush=True)

    # 3. Ispis paginacije
    print("\n🔢 === HTML PAGINACIJE ===")
    for p in soup.select(".pagination, .pagination-results, .results"):
        print(p.prettify(), flush=True)

    # 4. Ispis detalja sa stranice samog proizvoda (radi provjere stanja medija/omota VG+, EX...)
    if kartice:
        a_el = kartice[0].select_one("a[href*='product_id=']") or kartice[0].select_one("a[href]")
        if a_el:
            p_url = urljoin("https://freebird.hr", a_el["href"])
            print(f"\n💿 === DETALJI PROIZVODA: {p_url} ===", flush=True)
            r_p = s.get(p_url, timeout=30)
            soup_p = BeautifulSoup(r_p.text, "lxml")
            for blok in soup_p.select(".product-info, .product-stats, .product-details, #tab-description, .description"):
                print(blok.prettify()[:2500], flush=True)

if __name__ == "__main__":
    provjeri_html_kartice()
