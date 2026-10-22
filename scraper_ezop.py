import requests as std_requests
from bs4 import BeautifulSoup
import urllib3
import socket

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

r_kat = session.get("https://ezop-antikvarijat.hr/kategorija/glazba/", timeout=15)
soup = BeautifulSoup(r_kat.text, "lxml")

print("=== 1. FILTERI U SIDEBARU (<aside id='secondary'>) ===", flush=True)
aside = soup.select_one("#secondary")
if aside:
    for a in aside.find_all("a", href=True):
        print(f"Filter link: {a.get_text(' ', strip=True)} -> {a['href']}", flush=True)
    for inp in aside.find_all(["input", "select", "option"]):
        print(f"Filter polje: <{inp.name}> name={inp.get('name')} value={inp.get('value')} text={inp.get_text(strip=True)}", flush=True)

print("\n=== 2. CIJELI HTML PRVIH 3 KARTICA NA ARHIVI (BEZ REZANJA) ===", flush=True)
kartice = soup.select("div.product, li.product")
for i, k in enumerate(kartice[:3], 1):
    print(f"\n================ KARTICA #{i} ================", flush=True)
    print(str(k), flush=True)

print("\n=== 3. PROVJERA KARTICE ZA RASPRODANI (181787) I DOSTUPNI (207847) NA PRETRAZI ===", flush=True)
for pojam in ["U 02650 L", "LSY 62160"]:
    r_s = session.get(f"https://ezop-antikvarijat.hr/?s={pojam}&post_type=product", timeout=15)
    s_soup = BeautifulSoup(r_s.text, "lxml")
    k_s = s_soup.select_one("div.product, li.product")
    print(f"\n--- Rezultat pretrage za '{pojam}' ---", flush=True)
    if k_s:
        print(str(k_s), flush=True)
    else:
        print("Nije pronađena kartica u pretrazi, naslov stranice:", s_soup.title.string if s_soup.title else "Nema", flush=True)
