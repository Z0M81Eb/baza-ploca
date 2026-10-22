import requests as std_requests
from bs4 import BeautifulSoup
import urllib3
import socket
import json

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DOMAIN = "ezop-antikvarijat.hr"
ORIGIN_IP = "116.202.174.206"

# Direktno spajanje na Ezopov server (zaobilazi Cloudflare u 0.1s)
_ORIG_GETADDRINFO = socket.getaddrinfo
def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if host in (DOMAIN, f"www.{DOMAIN}"):
        return _ORIG_GETADDRINFO(ORIGIN_IP, port, socket.AF_INET, type, proto, flags)
    return _ORIG_GETADDRINFO(host, port, family, type, proto, flags)

socket.getaddrinfo = _patched_getaddrinfo

session = std_requests.Session()
session.verify = False
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
    "Accept-Language": "hr-HR,hr;q=0.9,en-US;q=0.8,en;q=0.7"
})

print("=== 1. TRAŽENJE KATEGORIJE GLAZBA I PODKATEGORIJA ===", flush=True)
r_wp_cat = session.get("https://ezop-antikvarijat.hr/wp-json/wp/v2/product_cat?slug=glazba", timeout=15)
print(f"WP v2 product_cat?slug=glazba (Status {r_wp_cat.status_code}):", flush=True)
glazba_id = None
try:
    cat_data = r_wp_cat.json()
    for c in cat_data:
        glazba_id = c.get("id")
        print(f" -> ID: {glazba_id} | Ime: {c.get('name')} | Count: {c.get('count')}", flush=True)
except Exception as e:
    print(f"Greška: {e} | Tekst: {r_wp_cat.text[:300]}", flush=True)

if glazba_id:
    r_sub = session.get(f"https://ezop-antikvarijat.hr/wp-json/wp/v2/product_cat?parent={glazba_id}&per_page=100", timeout=15)
    print(f"\nPodkategorije od Glazba (parent={glazba_id}):", flush=True)
    try:
        for sc in r_sub.json():
            print(f" -> Sub-ID: {sc.get('id')} | Slug: {sc.get('slug')} | Ime: {sc.get('name')} | Count: {sc.get('count')}", flush=True)
    except Exception:
        pass

print("\n=== 2. USPOREDBA API JSON-A: 181787 (NEMA NA STANJU) vs 207847 (IMA NA STANJU) ===", flush=True)
for pid, opis in [("181787", "NEMA NA STANJU"), ("207847", "IMA NA STANJU")]:
    url_api = f"https://ezop-antikvarijat.hr/wp-json/wc/store/products/{pid}"
    r_p = session.get(url_api, timeout=15)
    print(f"\n--- Store API za {pid} ({opis}) [Status: {r_p.status_code}] ---", flush=True)
    try:
        podaci = r_p.json()
        print(json.dumps(podaci, indent=2, ensure_ascii=False), flush=True)
    except Exception:
        print(r_p.text[:1000], flush=True)

print("\n=== 3. PROVJERA HTML ARHIVE (/kategorija/glazba/) ===", flush=True)
r_kat = session.get("https://ezop-antikvarijat.hr/kategorija/glazba/", timeout=15)
soup_kat = BeautifulSoup(r_kat.text, "lxml")
broj_stranica_el = soup_kat.select(".page-numbers")
zadnja_str = [el.get_text(strip=True) for el in broj_stranica_el]
print(f"Paginacija na /kategorija/glazba/: {zadnja_str}", flush=True)

kartice = soup_kat.select("li.product, div.product, article.product, .type-product")
print(f"Ukupno kartica na 1. stranici: {len(kartice)}", flush=True)
for i, k in enumerate(kartice[:3], 1):
    print(f"\n--- Kartica #{i} HTML ---", flush=True)
    print(str(k)[:700], flush=True)
