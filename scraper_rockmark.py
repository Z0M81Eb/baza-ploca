import cloudscraper
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import urllib3
import sys

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SITE_URL = "https://www.rockmark.hr"

def stvori_sesiju():
    s = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)",
        "Accept": "application/json, text/html, */*",
    })
    return s

def izvidnica_rockmark():
    s = stvori_sesiju()

    print(f"🔎 [1/4] Provjeravam skrivene WooCommerce API-je na {SITE_URL} ...", flush=True)
    api_urlovi = [
        "/wp-json/wc/store/v1/products?per_page=5",
        "/wp-json/wp/v2/product?per_page=5"
    ]
    for api_p in api_urlovi:
        try:
            r_api = s.get(SITE_URL + api_p, timeout=15)
            print(f"  -> {api_p}: HTTP {r_api.status_code} (Duljina: {len(r_api.text)})", flush=True)
            if r_api.status_code == 200 and r_api.text.strip().startswith(("[", "{")):
                print(f"  ✅ API OTVOREN! Uzorak JSON-a:\n{r_api.text[:300]}\n", flush=True)
        except Exception as e:
            print(f"  -> Greška na API {api_p}: {e}", flush=True)

    print(f"\n🔎 [2/4] Pretražujem API za nove kategorije ploča...", flush=True)
    try:
        r_cat = s.get(SITE_URL + "/wp-json/wc/store/v1/products/categories?per_page=100", timeout=15)
        if r_cat.status_code == 200:
            kategorije = r_cat.json()
            if isinstance(kategorije, list):
                print(f"  ✅ Pronađeno {len(kategorije)} kategorija. Filtriram ključne riječi...", flush=True)
                for k in kategorije:
                    ime = k.get("name", "").lower()
                    slug = k.get("slug", "").lower()
                    if any(x in ime or x in slug for x in ["lp", "vinil", "vinyl", "ploc", "ploč", "nosac", "zvuk"]):
                        print(f"     -> HIT: {k.get('name')} (ID: {k.get('id')}) - URL: {k.get('link')}", flush=True)
        else:
             print(f"  -> Kategorije API HTTP {r_cat.status_code}", flush=True)
    except Exception as e:
        print(f"  -> Greška kod API kategorija: {e}", flush=True)

    print(f"\n🔎 [3/4] Spajam se na naslovnicu i skeniram HTML linkove...", flush=True)
    try:
        r = s.get(SITE_URL, timeout=30)
        print(f"✅ Naslovnica dohvaćena! HTTP {r.status_code}", flush=True)
        soup = BeautifulSoup(r.text, "lxml")

        svi_linkovi = []
        vidjeni = set()

        for a in soup.select("a[href]"):
            href = a["href"].strip()
            tekst = " ".join(a.get_text(" ", strip=True).split())
            puni_url = urljoin(SITE_URL, href)
            
            if "rockmark.hr" in puni_url and puni_url not in vidjeni and tekst:
                vidjeni.add(puni_url)
                svi_linkovi.append((tekst, puni_url))

        vinil_kandidati = []
        for tekst, url in svi_linkovi:
            spojeno = f"{tekst} {url}".lower()
            if any(v in spojeno for v in ["vinyl", "vinil", "lp", "ploč", "ploc"]):
                vinil_kandidati.append((tekst, url))

        print(f"\n🎵 === PRONAĐENI HTML KANDIDATI ({len(vinil_kandidati)}) ===", flush=True)
        for idx, (tekst, url) in enumerate(vinil_kandidati[:20], 1):
            print(f"  * [{tekst}] -> {url}", flush=True)

    except Exception as e:
        print(f"❌ Greška pri analizi HTML-a: {e}", flush=True)

    print("\n🔎 [4/4] Izvidnica završena!", flush=True)

if __name__ == "__main__":
    izvidnica_rockmark()
