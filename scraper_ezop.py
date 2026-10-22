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
        s.proxies = {
            "http": "socks5://127.0.0.1:9050",
            "https": "socks5://127.0.0.1:9050"
        }
    return s

def podigni_tor():
    print("⚠️ Palim Tor tunel prema Origin IP-u (116.202.174.206)...", flush=True)
    subprocess.run(["sudo", "apt-get", "update", "-qq"], check=False, timeout=45)
    subprocess.run(["sudo", "apt-get", "install", "-y", "-qq", "tor"], check=False, timeout=60)
    subprocess.run(["sudo", "service", "tor", "restart"], check=False, timeout=15)
    time.sleep(6)

session = kreiraj_sesiju(koristi_tor=False)
tor_aktivan = False

def sigurni_get(url, timeout=35):
    global session, tor_aktivan
    for pokusaj in range(1, 4):
        try:
            t0 = time.time()
            r = session.get(url, timeout=timeout)
            print(f"[GET {r.status_code} za {time.time()-t0:.1f}s] {url}", flush=True)
            if r.status_code == 200:
                return r
            elif r.status_code in [522, 403, 503] and not tor_aktivan:
                podigni_tor()
                session = kreiraj_sesiju(koristi_tor=True)
                tor_aktivan = True
        except Exception as e:
            print(f"   [Timeout/Greška (pokušaj {pokusaj}/3) za {url}: {e}]", flush=True)
            if pokusaj == 2 and not tor_aktivan:
                podigni_tor()
                session = kreiraj_sesiju(koristi_tor=True)
                tor_aktivan = True
            time.sleep(2)
    return None

print("=== 1. KRAJ JSON-A ZA VARIJACIJE 315415 (NEMA) vs 306668 (IMA) ===", flush=True)
for var_id, opis in [("315415", "NEMA NA STANJU - parent 181787"), ("306668", "IMA NA STANJU - parent 207847")]:
    r_v = sigurni_get(f"https://ezop-antikvarijat.hr/wp-json/wc/store/products/{var_id}", timeout=30)
    if r_v and r_v.status_code == 200:
        try:
            d = r_v.json()
            d.pop("description", None)
            d.pop("short_description", None)
            d.pop("images", None)
            print(f"\n--- Varijacija {var_id} ({opis}) ---", flush=True)
            print(json.dumps(d, indent=2, ensure_ascii=False), flush=True)
        except Exception as e:
            print(f"JSON greška: {e}", flush=True)

print("\n=== 2. SIDEBAR FILTERI I LP KARTICE (erotski-omoti-glazba) ===", flush=True)
r_lp = sigurni_get("https://ezop-antikvarijat.hr/kategorija/glazba/erotski-omoti-glazba/", timeout=35)
if r_lp and r_lp.status_code == 200:
    soup = BeautifulSoup(r_lp.text, "lxml")

    aside = soup.select_one("#secondary")
    if aside:
        for sec in aside.select("section.widget"):
            sec_id = sec.get("id", "")
            if "product_categories" in sec_id:
                continue
            print(f"\n--- Widget: {sec_id} ---", flush=True)
            print(str(sec)[:1500], flush=True)

    kartice = soup.select("div.product")
    print(f"\nUkupno kartica na 1. stranici: {len(kartice)}", flush=True)

    ispisano_dostupnih = 0
    ispisano_nedostupnih = 0

    for k in kartice:
        je_lp = bool(k.select_one(".cat_music_lp"))
        ima_kupi = bool(k.select_one("a.kupi, a.add_to_cart_button"))
        ima_nedostupno = ("nedostupno" in str(k).lower()) or ("nema na stanju" in k.get_text(" ", strip=True).lower())

        if je_lp and ima_kupi and ispisano_dostupnih < 2:
            ispisano_dostupnih += 1
            print(f"\n================ DOSTUPNA LP KARTICA #{ispisano_dostupnih} ================", flush=True)
            print(str(k), flush=True)
        elif je_lp and (not ima_kupi or ima_nedostupno) and ispisano_nedostupnih < 2:
            ispisano_nedostupnih += 1
            print(f"\n================ NEDOSTUPNA LP KARTICA #{ispisano_nedostupnih} ================", flush=True)
            print(str(k), flush=True)

print("\n=== 3. PROVJERA POJEDINAČNE STRANICE 181787 (NEMA) vs 207847 (IMA) ===", flush=True)
for opis, url in [
    ("NEMA NA STANJU (181787)", "https://ezop-antikvarijat.hr/rock-n-roll-zg-1979-89-rocknroll-zg-1979-89-2/"),
    ("IMA NA STANJU (207847)", "https://ezop-antikvarijat.hr/parni-valjak-emc-2/")
]:
    r_p = sigurni_get(url, timeout=30)
    if r_p and r_p.status_code == 200:
        sp = BeautifulSoup(r_p.text, "lxml")
        blok = sp.select_one(".price-cart-block")
        print(f"\n--- {opis} (.price-cart-block) ---", flush=True)
        print(str(blok) if blok else "Nema bloka", flush=True)
