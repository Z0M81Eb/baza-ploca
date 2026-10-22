import requests as std_requests
from bs4 import BeautifulSoup
import urllib3
import socket
import time

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DOMAIN = "ezop-antikvarijat.hr"
ORIGIN_IP = "116.202.174.206"

_ORIG_GETADDRINFO = socket.getaddrinfo
def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if host in (DOMAIN, f"www.{DOMAIN}"):
        return _ORIG_GETADDRINFO(ORIGIN_IP, port, socket.AF_INET, type, proto, flags)
    return _ORIG_GETADDRINFO(host, port, family, type, proto, flags)

socket.getaddrinfo = _patched_getaddrinfo

session = std_requests.Session()
session.verify = False
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
})

def sigurni_get(url, timeout=60):
    for p in range(1, 4):
        try:
            t0 = time.time()
            r = session.get(url, timeout=timeout)
            print(f"[GET {r.status_code} za {time.time()-t0:.1f}s] {url}", flush=True)
            return r
        except Exception as e:
            print(f"[Pokušaj {p} nije uspio: {e}]", flush=True)
            time.sleep(2)
    return None

print("=== 1. BRZI TEST POJEDINAČNIH STRANICA (RASPRODANO vs DOSTUPNO) ===", flush=True)
for opis, url in [
    ("NEMA NA STANJU (181787)", "https://ezop-antikvarijat.hr/rock-n-roll-zg-1979-89-rocknroll-zg-1979-89-2/"),
    ("IMA NA STANJU (207847)", "https://ezop-antikvarijat.hr/parni-valjak-emc-2/")
]:
    r_p = sigurni_get(url, timeout=30)
    if r_p and r_p.status_code == 200:
        sp = BeautifulSoup(r_p.text, "lxml")
        blok = sp.select_one(".price-cart-block")
        print(f"\n--- {opis} (.price-cart-block) ---", flush=True)
        print(str(blok) if blok else "Nema .price-cart-block", flush=True)

print("\n=== 2. PROVJERA MANJE PODKATEGORIJE (/kategorija/glazba/kompilacije/) ===", flush=True)
# Kompilacije imaju 2089 artikala pa se učitavaju puno brže od glavne /glazba/
r_kat = sigurni_get("https://ezop-antikvarijat.hr/kategorija/glazba/kompilacije/", timeout=60)
if not r_kat:
    r_kat = sigurni_get("https://ezop-antikvarijat.hr/kategorija/glazba/", timeout=90)

if r_kat and r_kat.status_code == 200:
    soup = BeautifulSoup(r_kat.text, "lxml")

    print("\n--- FILTERI U SIDEBARU (<aside id='secondary'>) ---", flush=True)
    aside = soup.select_one("#secondary")
    if aside:
        print(str(aside)[:3500], flush=True)

    print("\n--- CIJELI HTML PRVIH 4 KARTICA NA ARHIVI (BEZ REZANJA) ---", flush=True)
    kartice = soup.select("div.product, li.product")
    for i, k in enumerate(kartice[:4], 1):
        print(f"\n================ KARTICA #{i} ================", flush=True)
        print(str(k), flush=True)

print("\n=== 3. PROVJERA WP REST API-JA ZA PROIZVODE (IMA LI META POLJA ZA STANJE/MEDIJ) ===", flush=True)
for pid in ["181787", "207847"]:
    r_wp = sigurni_get(f"https://ezop-antikvarijat.hr/wp-json/wp/v2/product/{pid}", timeout=20)
    if r_wp:
        print(f"WP v2 product/{pid} ({r_wp.status_code}): {r_wp.text[:1200]}", flush=True)
