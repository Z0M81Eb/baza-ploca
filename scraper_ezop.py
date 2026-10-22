import requests as std_requests
from bs4 import BeautifulSoup
import urllib3
import socket
import json

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

print("=== 1. KRAJ JSON-A ZA VARIJACIJE 315415 (NEMA) vs 306668 (IMA) ===", flush=True)
for var_id, opis in [("315415", "NEMA NA STANJU"), ("306668", "IMA NA STANJU")]:
    r_v = session.get(f"https://ezop-antikvarijat.hr/wp-json/wc/store/products/{var_id}", timeout=15)
    if r_v.status_code == 200:
        d = r_v.json()
        d.pop("description", None)
        d.pop("short_description", None)
        d.pop("images", None)
        print(f"\n--- Varijacija {var_id} ({opis}) ---", flush=True)
        print(json.dumps(d, indent=2, ensure_ascii=False), flush=True)

print("\n=== 2. SVI WIDGETI U SIDEBARU (OSIM KATEGORIJA) + LP KARTICE ===", flush=True)
r_lp = session.get("https://ezop-antikvarijat.hr/kategorija/glazba/erotski-omoti-glazba/", timeout=20)
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
print(f"\nUkupno kartica u erotski-omoti-glazba: {len(kartice)}", flush=True)

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

print("\n=== 3. PROVJERA POJEDINAČNE STRANICE 181787 vs 207847 ===", flush=True)
for opis, url in [
    ("NEMA NA STANJU (181787)", "https://ezop-antikvarijat.hr/rock-n-roll-zg-1979-89-rocknroll-zg-1979-89-2/"),
    ("IMA NA STANJU (207847)", "https://ezop-antikvarijat.hr/parni-valjak-emc-2/")
]:
    r_p = session.get(url, timeout=20)
    sp = BeautifulSoup(r_p.text, "lxml")
    blok = sp.select_one(".price-cart-block")
    print(f"\n--- {opis} (.price-cart-block) ---", flush=True)
    print(str(blok) if blok else "Nema bloka", flush=True)
