import cloudscraper
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import urllib3
import sys

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SITE_URL = "https://shop.dallas.si/hr"

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

def izvidnica_dallas():
    s = stvori_sesiju()

    # 1. Provjera robots.txt i Sitemapa
    print(f"🔎 [1/5] Provjeravam robots.txt i Sitemapove na {SITE_URL} ...", flush=True)
    for putanja in ["/robots.txt", "/sitemap.xml", "/1_index_sitemap.xml"]:
        try:
            r_rob = s.get("https://shop.dallas.si" + putanja, timeout=15)
            print(f"  -> {putanja}: HTTP {r_rob.status_code} (Duljina: {len(r_rob.text)})", flush=True)
            if r_rob.status_code == 200 and len(r_rob.text) > 20:
                print(r_rob.text[:1000], flush=True)
                print("  ----------------------------------------", flush=True)
        except Exception as e:
            print(f"  -> Greška na {putanja}: {e}", flush=True)

    # 2. Provjera javnih API-ja (PrestaShop)
    print(f"\n🔎 [2/5] Provjeravam skrivene JSON API-je ...", flush=True)
    api_urlovi = [
        "/api/products?output_format=JSON",
        "/module/ps_facetedsearch/ajax",
        "/wp-json/wc/store/v1/products?per_page=5"
    ]
    for api_p in api_urlovi:
        try:
            r_api = s.get(SITE_URL + api_p, timeout=15)
            print(f"  -> {api_p}: HTTP {r_api.status_code} (Duljina: {len(r_api.text)})", flush=True)
            if r_api.status_code == 200 and r_api.text.strip().startswith(("[", "{")):
                print(f"  ✅ API OTVOREN! Uzorak JSON-a:\n{r_api.text[:800]}\n", flush=True)
        except Exception as e:
            print(f"  -> Greška na API {api_p}: {e}", flush=True)

    # 3. Spajanje na naslovnicu i analiza navigacije
    print(f"\n🔎 [3/5] Spajam se na naslovnicu: {SITE_URL} ...", flush=True)
    try:
        r = s.get(SITE_URL, timeout=30)
    except Exception as e:
        print(f"❌ Greška pri spajanju na naslovnicu: {e}", flush=True)
        sys.exit(1)

    print(f"✅ Naslovnica dohvaćena! HTTP {r.status_code} | Duljina: {len(r.text)} znakova", flush=True)
    soup = BeautifulSoup(r.text, "lxml")

    svi_linkovi = []
    vidjeni = set()

    for a in soup.select("a[href]"):
        href = a["href"].strip()
        tekst = " ".join(a.get_text(" ", strip=True).split())
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue

        puni_url = urljoin(SITE_URL, href)
        if "dallas.si" not in puni_url.lower():
            continue

        kljuc = (puni_url, tekst)
        if kljuc not in vidjeni and tekst:
            vidjeni.add(kljuc)
            svi_linkovi.append((tekst, puni_url))

    kljucne_rijeci = ["vinyl", "vinil", "lp", "ploč", "ploc", "kategorij", "category"]
    vinil_kandidati = []

    for tekst, url in svi_linkovi:
        spojeno = f"{tekst} {url}".lower()
        if any(v in spojeno for v in kljucne_rijeci):
            vinil_kandidati.append((tekst, url))

    print(f"\n🎵 === UŽI IZBOR: KANDIDATI ZA KATEGORIJE ({len(vinil_kandidati)}) ===", flush=True)
    for idx, (tekst, url) in enumerate(vinil_kandidati[:20], 1):
        print(f"  * [{tekst}] -> {url}", flush=True)

    # Odabir testne kategorije (tražimo kategoriju za ploče/LP)
    test_url = None
    for tekst, url in vinil_kandidati:
        u_low = url.lower()
        if url.rstrip("/") != SITE_URL.rstrip("/") and any(x in u_low for x in ["vinyl", "vinil", "lp"]):
            test_url = url
            break

    if not test_url and vinil_kandidati:
        test_url = vinil_kandidati[0][1]

    if not test_url:
        print("\n⚠️ Nisam pronašao očitu kategoriju za ploče. Prekidam analizu HTML-a.", flush=True)
        return

    # 4. Dubinska analiza odabrane kategorije
    print(f"\n🔎 [4/5] Otvaram testnu kategoriju: {test_url}", flush=True)
    r_kat = s.get(test_url, timeout=30)
    print(f"✅ Kategorija dohvaćena! HTTP {r_kat.status_code}", flush=True)
    soup_kat = BeautifulSoup(r_kat.text, "lxml")

    selektori_kartica = [
        "article.product-miniature", ".ajax_block_product", "li.product", "div.product", 
        "div.product-grid-item", ".product-layout", ".product-item", ".item"
    ]

    pronadjene_kartice = []
    koristeni_selektor = ""
    for sel in selektori_kartica:
        el = soup_kat.select(sel)
        if len(el) >= 3:
            pronadjene_kartice = el
            koristeni_selektor = sel
            break

    print(f"\n📦 === ANALIZA KARTICE PROIZVODA (Selektor: '{koristeni_selektor}', Ukupno: {len(pronadjene_kartice)}) ===", flush=True)
    if pronadjene_kartice:
        print("--- KARTICA #1 HTML ---", flush=True)
        print(pronadjene_kartice[0].prettify()[:3000], flush=True)
    else:
        print("⚠️ Kartice nisu detektirane. Ispisujem glavni blok:", flush=True)
        glavni = soup_kat.select_one("main, #main, #content, .site-content, .shop-container, #center_column, #products") or soup_kat.body
        if glavni:
            print(glavni.prettify()[:3000], flush=True)

    # 5. Analiza paginacije i pojedinačnog proizvoda
    print("\n🔢 === ANALIZA PAGINACIJE ===", flush=True)
    paginacija = soup_kat.select(".pagination, .page-list, .woocommerce-pagination, .page-numbers")
    if paginacija:
        for p in paginacija:
            print(p.prettify()[:1500], flush=True)
    else:
        page_linkovi = [urljoin(SITE_URL, a["href"]) for a in soup_kat.select('a[href*="/page/"], a[href*="page="], a[href*="p="]')]
        print(f"Linkovi s paginacijom: {page_linkovi[:10]}", flush=True)

    if pronadjene_kartice:
        prvi_a = pronadjene_kartice[0].select_one("a[href]")
        if prvi_a:
            art_url = urljoin(SITE_URL, prvi_a["href"])
            print(f"\n💿 [5/5] === DETALJI PROIZVODA: {art_url} ===", flush=True)
            r_art = s.get(art_url, timeout=30)
            soup_art = BeautifulSoup(r_art.text, "lxml")
            for blok in soup_art.select(".product-description, .product-information, #tab-description, .page-content"):
                print(blok.prettify()[:2000], flush=True)

if __name__ == "__main__":
    izvidnica_dallas()
