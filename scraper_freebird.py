import cloudscraper
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import urllib3
import re
import sys

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SITE_URL = "https://freebird.hr"

def stvori_sesiju():
    s = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "hr-HR,hr;q=0.9,en-US;q=0.8,en;q=0.7",
    })
    return s

def izvidnica_freebird():
    s = stvori_sesiju()
    print(f"🔎 [1/3] Spajam se na naslovnicu: {SITE_URL} ...", flush=True)

    try:
        r = s.get(SITE_URL, timeout=30)
    except Exception as e:
        print(f"❌ Greška pri spajanju na naslovnicu: {e}", flush=True)
        sys.exit(1)

    print(f"✅ Naslovnica dohvaćena! HTTP {r.status_code} | Duljina: {len(r.text)} znakova", flush=True)
    soup = BeautifulSoup(r.text, "lxml")

    # 1. Analiza svih linkova u navigaciji / meniju
    svi_linkovi = []
    vidjeni = set()

    for a in soup.select("a[href]"):
        href = a["href"].strip()
        tekst = " ".join(a.get_text(" ", strip=True).split())
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue

        puni_url = urljoin(SITE_URL, href)
        if not puni_url.startswith(SITE_URL):
            continue

        # Preskačemo košaricu, prijavu, info stranice i sl.
        if any(x in puni_url.lower() for x in ["route=account", "route=checkout", "route=information", "login", "register", "contact", "cart"]):
            continue

        kljuc = (puni_url, tekst)
        if kljuc not in vidjeni and tekst:
            vidjeni.add(kljuc)
            svi_linkovi.append((tekst, puni_url))

    # Filtriramo vjerojatne kategorije (OpenCart path= ili ključne riječi za glazbu/ploče)
    kljucne_rijeci = ["lp", "vinyl", "vinil", "ploč", "ploc", "12", "7", "10", "singl", "rock", "jazz", "pop", "metal", "ex yu", "ex-yu", "yu", "blues", "soul", "punk", "funk", "klasik", "ostalo", "kategorij", "path="]
    kandidati_kategorija = []
    vinil_kandidati = []

    for tekst, url in svi_linkovi:
        spojeno = f"{tekst} {url}".lower()
        if any(k in spojeno for k in kljucne_rijeci) or "path=" in url.lower() or url.count("/") >= 3:
            kandidati_kategorija.append((tekst, url))
        if any(v in spojeno for v in ["lp", "vinyl", "vinil", "ploč", "ploc", '12"', '7"', '10"', "12''", "7''", "singl"]):
            vinil_kandidati.append((tekst, url))

    print(f"\n📂 === PRONAĐENI LINKOVI U MENIJU (Ukupno filtrirano: {len(kandidati_kategorija)}) ===", flush=True)
    for idx, (tekst, url) in enumerate(kandidati_kategorija[:120], 1):
        print(f"  {idx:03d}. [{tekst}] -> {url}", flush=True)

    print(f"\n🎵 === UŽI IZBOR: VINIL / LP KANDIDATI ({len(vinil_kandidati)}) ===", flush=True)
    for idx, (tekst, url) in enumerate(vinil_kandidati[:60], 1):
        print(f"  * [{tekst}] -> {url}", flush=True)

    # 2. Ako su podkategorije skrivene unutar glavne Vinil stranice, otvaramo prvu glavnu vinil kategoriju
    test_url = None
    for tekst, url in vinil_kandidati:
        if url.rstrip("/") != SITE_URL.rstrip("/"):
            test_url = url
            break

    if not test_url and kandidati_kategorija:
        for tekst, url in kandidati_kategorija:
            if url.rstrip("/") != SITE_URL.rstrip("/"):
                test_url = url
                break

    if not test_url:
        print("\n⚠️ Nisam pronašao nijedan link kategorije na naslovnici. Ispisujem prvih 2000 znakova bodyja:", flush=True)
        if soup.body:
            print(soup.body.prettify()[:2000], flush=True)
        return

    print(f"\n🔎 [2/3] Otvaram testnu kategoriju za dubinsku analizu: {test_url}", flush=True)
    r_kat = s.get(test_url, timeout=30)
    print(f"✅ Kategorija dohvaćena! HTTP {r_kat.status_code} | Duljina: {len(r_kat.text)} znakova", flush=True)
    soup_kat = BeautifulSoup(r_kat.text, "lxml")

    # Provjera podkategorija unutar same otvorene kategorije (npr. lijevi sidebar ili popis podkategorija)
    pod_linkovi = []
    for a in soup_kat.select("a[href]"):
        href = urljoin(SITE_URL, a["href"].strip())
        tekst = " ".join(a.get_text(" ", strip=True).split())
        if href.startswith(SITE_URL) and tekst and (tekst, href) not in vidjeni:
            if any(x in href.lower() for x in ["path=", "lp", "vinyl", "vinil", "12", "7", "10", "rock", "jazz", "pop", "yu"]):
                pod_linkovi.append((tekst, href))
                vidjeni.add(( href, tekst))

    if pod_linkovi:
        print(f"\n🌿 === DODATNE PODKATEGORIJE UNUTAR '{test_url}' ({len(pod_linkovi)}) ===", flush=True)
        for idx, (tekst, url) in enumerate(pod_linkovi[:80], 1):
            print(f"  Sub-{idx:02d}. [{tekst}] -> {url}", flush=True)

    # 3. Ispis HTML strukture proizvoda i paginacije
    selektori_kartica = [
        ".product-layout",
        ".product-thumb",
        ".product-grid > div",
        ".product-list > div",
        ".product-item",
        ".box-product > div",
        "div[class*='product']"
    ]

    pronadjene_kartice = []
    koristeni_selektor = ""
    for sel in selektori_kartica:
        el = soup_kat.select(sel)
        if len(el) >= 3:
            pronadjene_kartice = el
            koristeni_selektor = sel
            break

    # Ako prva vinil stranica sadrži samo listu podkategorija (bez proizvoda), ulazimo u prvu podkategoriju
    if not pronadjene_kartice and pod_linkovi:
        sub_url = pod_linkovi[0][1]
        print(f"\n🔎 Glavna kategorija je samo razdjelnik. Ulazim u podkategoriju: {sub_url}", flush=True)
        r_sub = s.get(sub_url, timeout=30)
        soup_kat = BeautifulSoup(r_sub.text, "lxml")
        for sel in selektori_kartica:
            el = soup_kat.select(sel)
            if len(el) >= 2:
                pronadjene_kartice = el
                koristeni_selektor = sel
                break

    print(f"\n📦 [3/3] === ANALIZA KARTICE PROIZVODA (Selektor: '{koristeni_selektor}', Ukupno na stranici: {len(pronadjene_kartice)}) ===", flush=True)
    if pronadjene_kartice:
        print("--- KARTICA #1 HTML ---", flush=True)
        print(pronadjene_kartice[0].prettify()[:3500], flush=True)
        if len(pronadjene_kartice) > 1:
            print("\n--- KARTICA #2 HTML ---", flush=True)
            print(pronadjene_kartice[1].prettify()[:2500], flush=True)
    else:
        print("⚠️ Standardne kartice nisu detektirane. Ispisujem središnji #content blok:", flush=True)
        content_div = soup_kat.select_one("#content, #column-right, main, .main-content") or soup_kat.body
        if content_div:
            print(content_div.prettify()[:4500], flush=True)

    # 4. Analiza paginacije
    print("\n🔢 === ANALIZA PAGINACIJE ===", flush=True)
    paginacija = soup_kat.select(".pagination, .links, .results, ul.pagination")
    if paginacija:
        for p in paginacija:
            print(p.prettify(), flush=True)
    else:
        page_linkovi = [urljoin(SITE_URL, a["href"]) for a in soup_kat.select('a[href*="page="], a[href*="/page/"]')]
        print(f"Linkovi s 'page': {page_linkovi[:10]}", flush=True)

    # 5. Ako u kartici na katalogu nema stanja medija/omota (VG+, EX, NM), provjeravamo pojedinačni proizvod
    if pronadjene_kartice:
        prvi_link_el = pronadjene_kartice[0].select_one("a[href]")
        if prvi_link_el:
            artikl_url = urljoin(SITE_URL, prvi_link_el["href"])
            print(f"\n💿 === PROVJERA POJEDINAČNOG PROIZVODA (Stanje medija/omota): {artikl_url} ===", flush=True)
            r_art = s.get(artikl_url, timeout=30)
            soup_art = BeautifulSoup(r_art.text, "lxml")
            art_info = soup_art.select_one("#content, .product-info, .product-details")
            if art_info:
                print(art_info.prettify()[:4000], flush=True)

if __name__ == "__main__":
    izvidnica_freebird()
