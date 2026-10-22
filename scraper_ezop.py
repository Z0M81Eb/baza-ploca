import requests as std_requests
from bs4 import BeautifulSoup
import urllib3
import socket
import subprocess
import json
import time

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DOMAIN = "ezop-antikvarijat.hr"
ORIGIN_IP = "116.202.174.206"

# Lokalno preusmjeravanje domene na stvarni Origin IP (zaobilazi Cloudflare)
_ORIG_GETADDRINFO = socket.getaddrinfo
def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if host in (DOMAIN, f"www.{DOMAIN}"):
        return _ORIG_GETADDRINFO(ORIGIN_IP, port, socket.AF_INET, type, proto, flags)
    return _ORIG_GETADDRINFO(host, port, family, type, proto, flags)

socket.getaddrinfo = _patched_getaddrinfo

def kreiraj_sesiju(koristi_tor=False):
    s = std_requests.Session()
    s.verify = False
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
        "Accept-Language": "hr-HR,hr;q=0.9,en-US;q=0.8,en;q=0.7"
    })
    if koristi_tor:
        # socks5:// (bez 'h') koristi naš _patched_getaddrinfo i spaja se preko Tora direktno na 116.202.174.206!
        s.proxies = {
            "http": "socks5://127.0.0.1:9050",
            "https": "socks5://127.0.0.1:9050"
        }
    return s

def podigni_tor():
    print("⚠️ Direktni IP vraća 522/timeout — palim Tor tunel direktno prema Origin IP-u (116.202.174.206)...", flush=True)
    subprocess.run(["sudo", "apt-get", "update", "-qq"], check=False, timeout=45)
    subprocess.run(["sudo", "apt-get", "install", "-y", "-qq", "tor"], check=False, timeout=60)
    subprocess.run(["sudo", "service", "tor", "restart"], check=False, timeout=15)
    time.sleep(6)

session = kreiraj_sesiju(koristi_tor=False)

# Brzi test na laganom JSON endpointu (kategorija 418 ima samo 84 artikla)
test_url = "https://ezop-antikvarijat.hr/wp-json/wp/v2/product_cat/418"
try:
    r_test = session.get(test_url, timeout=10)
    if r_test.status_code != 200:
        raise Exception(f"HTTP {r_test.status_code}")
    print("✅ Direktni spoj na 116.202.174.206 radi trenutno!", flush=True)
except Exception as e:
    print(f"ℹ️ Direktni test ({e}). Prebacujem na Tor -> Origin IP...", flush=True)
    podigni_tor()
    session = kreiraj_sesiju(koristi_tor=True)

def brzi_get(url, timeout=18):
    try:
        t0 = time.time()
        r = session.get(url, timeout=timeout)
        print(f"[GET {r.status_code} za {time.time()-t0:.1f}s] {url}", flush=True)
        return r
    except Exception as e:
        print(f"[Greška za {url}: {e}]", flush=True)
        return None

print("\n=== 1. PROVJERA VARIJACIJA U STORE API-JU: 315415 (NEMA) vs 306668 (IMA) ===", flush=True)
for var_id, opis in [("315415", "Varijacija od 181787 - NEMA NA STANJU"), ("306668", "Varijacija od 207847 - IMA NA STANJU")]:
    r_v = brzi_get(f"https://ezop-antikvarijat.hr/wp-json/wc/store/products/{var_id}")
    if r_v and r_v.status_code == 200:
        print(f"--- {opis} ---", flush=True)
        print(r_v.text[:1200], flush=True)

print("\n=== 2. PROVJERA WP V2 API-JA ZA PROIZVODE (181787 vs 207847) ===", flush=True)
for pid, opis in [("181787", "NEMA NA STANJU"), ("207847", "IMA NA STANJU")]:
    r_wp = brzi_get(f"https://ezop-antikvarijat.hr/wp-json/wp/v2/product/{pid}")
    if r_wp and r_wp.status_code == 200:
        data = r_wp.json()
        # Ispisujemo sve ključeve osim dugog opisa da vidimo meta polja, class_list i taksonomije
        data.pop("content", None)
        data.pop("excerpt", None)
        data.pop("yoast_head", None)
        data.pop("yoast_head_json", None)
        print(f"\n--- WP v2 za {pid} ({opis}) ---", flush=True)
        print(json.dumps(data, indent=2, ensure_ascii=False), flush=True)

print("\n=== 3. PROVJERA SVIH TAKSONOMIJA NA EZOPU ===", flush=True)
r_tax = brzi_get("https://ezop-antikvarijat.hr/wp-json/wp/v2/taxonomies")
if r_tax and r_tax.status_code == 200:
    print(r_tax.text[:1500], flush=True)

print("\n=== 4. CIJELI HTML KARTICE U MALOJ PODKATEGORIJI (slusaj-najglasnije - samo 84 artikla!) ===", flush=True)
r_mala = brzi_get("https://ezop-antikvarijat.hr/kategorija/glazba/slusaj-najglasnije/", timeout=25)
if r_mala and r_mala.status_code == 200:
    soup = BeautifulSoup(r_mala.text, "lxml")
    kartice = soup.select("div.product, li.product")
    print(f"Pronađeno kartica: {len(kartice)}", flush=True)
    for i, k in enumerate(kartice[:3], 1):
        print(f"\n================ KARTICA #{i} ================", flush=True)
        print(str(k), flush=True)

    aside = soup.select_one("#secondary")
    if aside:
        print("\n================ SIDEBAR FILTERI ================", flush=True)
        print(str(aside)[:2500], flush=True)
