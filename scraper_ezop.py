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

print("=== 1. ŠTA JE NA STRANICI PROIZVODA KOJI IMA GUMB 'IZABERI' (152795) I BEZ PURCHASABLE (453515)? ===", flush=True)
for opis, url in [
    ("Gumb IZABERI (152795)", "https://ezop-antikvarijat.hr/16-top-10-hits-vol-2-16-top-10-hits-vol-2-2/"),
    ("Bez purchasable (453515)", "https://ezop-antikvarijat.hr/a-l-lloyd-martyn-wyndham-reade-and-trevor-lucas-the-great-australian-legend-a-panorama-of-bush-balladry-and-song/")
]:
    r = session.get(url, timeout=30)
    sp = BeautifulSoup(r.text, "lxml")
    blok = sp.select_one(".price-cart-block")
    print(f"\n--- {opis} [Status {r.status_code}] ---", flush=True)
    print(str(blok) if blok else "Nema .price-cart-block", flush=True)

print("\n=== 2. ANALIZA SVIH 25 KARTICA NA 1. STRANICI (erotski-omoti-glazba) ===", flush=True)
r_arh = session.get("https://ezop-antikvarijat.hr/kategorija/glazba/erotski-omoti-glazba/", timeout=30)
soup = BeautifulSoup(r_arh.text, "lxml")
kartice = soup.select("div.product")

for i, k in enumerate(kartice, 1):
    klase = " ".join(k.get("class", []))
    je_lp = bool(k.select_one(".cat_music_lp"))
    je_cd = bool(k.select_one(".cat_music_cd"))
    ima_kupi = bool(k.select_one("a.kupi"))
    ima_izaberi = bool(k.select_one("a.izaberi"))
    ima_nedostupno = bool(k.select_one(".nedostupno")) or ("nema na stanju" in k.get_text(" ", strip=True).lower())
    dolje = k.select_one(".arhiva-dolje")
    dolje_txt = dolje.get_text(" ", strip=True) if dolje else "NEMA .arhiva-dolje"
    print(f"#{i:02d} | LP:{je_lp} CD:{je_cd} | kupi:{ima_kupi} izaberi:{ima_izaberi} nedostupno:{ima_nedostupno} | Dolje: {dolje_txt}", flush=True)
    if not ima_kupi and not ima_izaberi:
        print("   -> HTML kartice bez gumba:", str(k)[:800], flush=True)
