import cloudscraper
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import urllib3
import re
import sys

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SITE_URL = "https://www.dirtyoldempire.com"


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


def izvidnica_dirtyold():
    s = stvori_sesiju()

    # 1. Provjera robots.txt i Sitemapa
    print(f"🔎 [1/5] Provjeravam robots.txt i Sitemap na {SITE_URL} ...", flush=True)
    for putanja in ["/robots.txt", "/sitemap_index.xml", "/sitemap.xml", "/wp-sitemap.xml"]:
        try:
            r_rob = s.get(SITE_URL + putanja, timeout=15)
            print(f"  -> {putanja}: HTTP {r_rob.status_code} (Duljina: {len(r_rob.text)})", flush=True)
            if r_rob.status_code == 200 and len(r_rob.text) > 20:
                if "robots.txt" in putanja or "xml" in putanja:
                    print(r_rob.text[:1200], flush=True)
                    print("  ----------------------------------------", flush=True)
        except Exception as e:
            print(f"  -> Greška na {putanja}: {e}", flush=True)

    # 2. Provjera javnog WooCommerce Store API-ja (najbrži JSON pristup ako je riječ o WooCommerceu)
    print(f"\n🔎 [2/5] Provjeravam javni WooCommerce API ...", flush=True)
    api_urlovi = [
        "/wp-json/wc/store/v1/products?per_page=5",
        "/wp-json/wc/store/v1/products/categories?per_page=100",
        "/wp-json/wp/v2/product_cat?per_page=100"
    ]
    for api_p in api_urlovi:
        try:
            r_api = s.get(SITE_URL + api_p, timeout=15)
            print(f"  -> {api_p}: HTTP {r_api.status_code} (Duljina: {len(r_api.text)})", flush=True)
            if r_api.status_code == 200 and r_api.text.strip().startswith(("[", "{")):
                print(f"  ✅ API OTVOREN! Uzorak JSON-a:\n{r_api.text[:1000]}\n", flush=True)
        except Exception as e:
            print(f"  -> Greška na API {api_p}: {e}", flush=True)

    # 3. Spajanje na naslovnicu i analiza navigacije / kategorija
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
        if "dirtyoldempire.com" not in puni_url:
            continue

        if any(x in puni_url.lower() for x in ["my-account", "moj-racun", "cart", "kosarica", "checkout", "blagajna", "login", "kontakt", "wp-login"]):
            continue

        kljuc = (puni_url, tekst)
        if kljuc not in vidjeni and (tekst or "category" in puni_url.lower() or "kategorija" in puni_url.lower()):
            vidjeni.add(kljuc)
            svi_linkovi.append((tekst or "[Bez teksta]", puni_url))

    kljucne_rijeci = [
        "vinyl", "vinil", "lp", "ploč", "ploc", "glazb", "music", "cd", "singl",
        "7\"", "12\"", "10\"", "kategorij", "category", "shop", "trgovina",
        "rock", "punk", "metal", "hip-hop", "jazz", "pop", "ex-yu", "domać", "stran"
    ]
    kandidati_kategorija = []
    vinil_kandidati = []

    for tekst, url in svi_linkovi:
        spojeno = f"{tekst} {url}".lower()
        if any(k in spojeno for k in kljucne_rijeci):
            kandidati_kategorija.append((tekst, url))
        if any(v in spojeno for v in ["vinyl", "vinil", "lp", "ploč", "ploc", "singl", "glazb", "music"]):
            vinil_kandidati.append((tekst, url))

    print(f"\n📂 === PRONAĐENI LINKOVI U MENIJU (Ukupno filtrirano: {len(kandidati_kategorija)}) ===", flush=True)
    for idx, (tekst, url) in enumerate(kandidati_kategorija[:120], 1):
        print(f"  {idx:03d}. [{tekst}] -> {url}", flush=True)

    print(f"\n🎵 === UŽI IZBOR: VINIL / GLAZBA KANDIDATI ({len(vinil_kandidati)}) ===", flush=True)
    for idx, (tekst, url) in enumerate(vinil_kandidati[:60], 1):
        print(f"  * [{tekst}] -> {url}", flush=True)

    # Odabir najbolje testne kategorije (izbjegavamo naslovnicu i dokumente)
    test_url = None
    for tekst, url in vinil_kandidati:
        u_low = url.lower()
        if url.rstrip("/") != SITE_URL.rstrip("/") and not u_low.endswith((".xlsx", ".pdf", ".jpg", ".png")):
            if any(x in u_low for x in ["vinyl", "vinil", "lp", "ploc", "glazb", "kategorija", "category"]):
                test_url = url
                break

    if not test_url and vinil_kandidati:
        for tekst, url in vinil_kandidati:
            if url.rstrip("/") != SITE_URL.rstrip("/"):
                test_url = url
                break

    if not test_url:
        print("\n⚠️ Nisam pronašao očitu vinil kategoriju na naslovnici.", flush=True)
        return

    # 4. Dubinska analiza odabrane kategorije
    print(f"\n🔎 [4/5] Otvaram testnu kategoriju za dubinsku analizu: {test_url}", flush=True)
    r_kat = s.get(test_url, timeout=30)
    print(f"✅ Kategorija dohvaćena! HTTP {r_kat.status_code} | Duljina: {len(r_kat.text)} znakova", flush=True)
    soup_kat = BeautifulSoup(r_kat.text, "lxml")

    # Provjera podkategorija ili filtera unutar kategorije
    pod_linkovi = []
    for a in soup_kat.select("a[href]"):
        href = urljoin(SITE_URL, a["href"].strip())
        tekst = " ".join(a.get_text(" ", strip=True).split())
        if "dirtyoldempire.com" in href and tekst and (href, tekst) not in vidjeni:
            if any(x in href.lower() for x in ["kategorija", "category", "vinyl", "vinil", "lp", "ploc", "glazb", "filter", "format"]):
                pod_linkovi.append((tekst, href))
                vidjeni.add((href, tekst))

    if pod_linkovi:
        print(f"\n🌿 === PODKATEGORIJE / FILTERI UNUTAR '{test_url}' ({len(pod_linkovi)}) ===", flush=True)
        for idx, (tekst, url) in enumerate(pod_linkovi[:80], 1):
            print(f"  Sub-{idx:02d}. [{tekst}] -> {url}", flush=True)

    selektori_kartica = [
        "li.product",
        "div.product-grid-item",
        "div.product-small",
        "div.product",
        ".products .product",
        ".product-layout",
        ".product-thumb",
        ".product-item",
        "article.product"
    ]

    pronadjene_kartice = []
    koristeni_selektor = ""
    for sel in selektori_kartica:
        el = soup_kat.select(sel)
        if len(el) >= 3:
            pronadjene_kartice = el
            koristeni_selektor = sel
            break

    print(f"\n📦 === ANALIZA KARTICE PROIZVODA (Selektor: '{koristeni_selektor}', Ukupno na stranici: {len(pronadjene_kartice)}) ===", flush=True)
    if pronadjene_kartice:
        print("--- KARTICA #1 HTML ---", flush=True)
        print(pronadjene_kartice[0].prettify()[:3500], flush=True)
        if len(pronadjene_kartice) > 1:
            print("\n--- KARTICA #2 HTML ---", flush=True)
            print(pronadjene_kartice[1].prettify()[:2500], flush=True)
    else:
        print("⚠️ Standardne kartice nisu detektirane. Ispisujem središnji dio stranice:", flush=True)
        glavni = soup_kat.select_one("main, #main, #content, .site-content, .shop-container") or soup_kat.body
        if glavni:
            print(glavni.prettify()[:4000], flush=True)

    print("\n🔢 === ANALIZA PAGINACIJE ===", flush=True)
    paginacija = soup_kat.select(".woocommerce-pagination, nav.pagination, .pagination, .page-numbers, .woocommerce-result-count")
    if paginacija:
        for p in paginacija:
            print(p.prettify()[:2000], flush=True)
    else:
        page_linkovi = [urljoin(SITE_URL, a["href"]) for a in soup_kat.select('a[href*="/page/"], a[href*="page="], a[href*="paged="]')]
        print(f"Linkovi s 'page': {page_linkovi[:10]}", flush=True)

    # 5. Provjera pojedinačnog proizvoda
    if pronadjene_kartice:
        prvi_a = pronadjene_kartice[0].select_one("a[href]")
        if prvi_a:
            art_url = urljoin(SITE_URL, prvi_a["href"])
            print(f"\n💿 [5/5] === DETALJI POJEDINAČNOG PROIZVODA: {art_url} ===", flush=True)
            r_art = s.get(art_url, timeout=30)
            soup_art = BeautifulSoup(r_art.text, "lxml")
            for blok in soup_art.select(".summary, .product-info, .product-short-description, .woocommerce-product-attributes, .product_meta, #tab-description"):
                print(blok.prettify()[:2000], flush=True)


if __name__ == "__main__":
    izvidnica_dirtyold()
