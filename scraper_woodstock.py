import cloudscraper
from bs4 import BeautifulSoup
import urllib3
import re

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SITE_URL = "https://www.woodstock-trgovina.hr/hr/gramofonske-ploce/p-2"

def provjeri_html_kartice():
    s = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "desktop": True})
    print(f"🔎 Otvaram glavnu kategoriju ploča: {SITE_URL}", flush=True)
    r = s.get(SITE_URL, timeout=30)
    print(f"✅ Status: {r.status_code} | Duljina: {len(r.text)}", flush=True)

    soup = BeautifulSoup(r.text, "lxml")

    # Pronalazak glavnog grid containera (najvjerojatnije div class row)
    kartice = []
    svi_divovi = soup.select("div[class]")
    for el in svi_divovi:
        klase = el.get("class", [])
        # Tražimo divove koji imaju u sebi cijenu ili naslov
        if el.select_one("img") and (el.select_one("h3") or el.select_one("h4") or el.select_one("a.h4") or "EUR" in el.text):
            if any("col-" in cls for cls in klase) or "item" in klase:
                if el not in kartice and len(el.text) < 500:
                    kartice.append(el)

    print(f"\n📦 Pronađeno kartica na stranici: {len(kartice)}", flush=True)
    if kartice:
        print("\n=== HTML PRVE KARTICE ===")
        print(kartice[0].prettify(), flush=True)

    # Analiza paginacije
    print("\n🔢 === HTML PAGINACIJE ===")
    for p in soup.select("ul.pagination, .pagination"):
        print(p.prettify(), flush=True)

if __name__ == "__main__":
    provjeri_html_kartice()
