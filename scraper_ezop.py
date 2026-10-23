import requests as std_requests
from bs4 import BeautifulSoup
import urllib3
import socket
import subprocess
import time

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DOMAIN = "ezop-antikvarijat.hr"
# Sve IP adrese servera otkrivene u SPF zapisu
ORIGIN_IPS = [
    "116.202.174.206",
    "178.218.162.238",
    "144.76.118.138",
    "144.76.95.178",
    "144.76.37.175"
]
AKTIVNI_IP = ORIGIN_IPS[0]

_ORIG_GETADDRINFO = socket.getaddrinfo
def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if host in (DOMAIN, f"www.{DOMAIN}"):
        return _ORIG_GETADDRINFO(AKTIVNI_IP, port, socket.AF_INET, type, proto, flags)
    return _ORIG_GETADDRINFO(host, port, family, type, proto, flags)

socket.getaddrinfo = _patched_getaddrinfo

def kreiraj_sesiju(koristi_tor=False):
    s = std_requests.Session()
    s.verify = False
    # Puna zaglavlja koja su u 10:27:44 radila za 0.7s!
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
        "Accept-Language": "hr-HR,hr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://ezop-antikvarijat.hr/",
        "Connection": "keep-alive"
    })
    if koristi_tor:
        s.proxies = {
            "http": "socks5://127.0.0.1:9050",
            "https": "socks5://127.0.0.1:9050"
        }
    return s

def podigni_tor():
    print("⚠️ Palim Tor tunel prema Origin IP-u...", flush=True)
    subprocess.run(["sudo", "apt-get", "update", "-qq"], check=False, timeout=45)
    subprocess.run(["sudo", "apt-get", "install", "-y", "-qq", "tor"], check=False, timeout=60)
    subprocess.run(["sudo", "service", "tor", "restart"], check=False, timeout=15)
    time.sleep(6)

session = kreiraj_sesiju(koristi_tor=False)
tor_aktivan = False

def sigurni_get(url, timeout=25):
    global session, tor_aktivan, AKTIVNI_IP
    for pokusaj in range(1, 5):
        try:
            t0 = time.time()
            r = session.get(url, timeout=timeout)
            print(f"[GET {r.status_code} za {time.time()-t0:.1f}s | IP: {AKTIVNI_IP}] {url}", flush=True)
            if r.status_code == 200:
                return r
            if r.status_code in [522, 403, 503]:
                if not tor_aktivan:
                    podigni_tor()
                    session = kreiraj_sesiju(koristi_tor=True)
                    tor_aktivan = True
                else:
                    subprocess.run(["sudo", "service", "tor", "reload"], check=False, timeout=10)
                    time.sleep(3)
        except Exception as e:
            print(f"   [Greška (pokušaj {pokusaj}): {e}]", flush=True)
            if not tor_aktivan:
                podigni_tor()
                session = kreiraj_sesiju(koristi_tor=True)
                tor_aktivan = True
            time.sleep(2)
    return None

# 0. Brzo zagrijavanje keep-alive konekcije (isto kao u 10:27:44)
sigurni_get("https://ezop-antikvarijat.hr/wp-json/wc/store/products/315415", timeout=15)

print("\n=== 1. PROVJERA KARTICA NA ARHIVI (erotski-omoti-glazba) ===", flush=True)
r_arh = sigurni_get("https://ezop-antikvarijat.hr/kategorija/glazba/erotski-omoti-glazba/", timeout=30)
izaberi_linkovi = []

if r_arh and r_arh.status_code == 200:
    soup = BeautifulSoup(r_arh.text, "lxml")
    kartice = soup.select("div.product")
    print(f"Ukupno kartica na stranici: {len(kartice)}", flush=True)

    for i, k in enumerate(kartice, 1):
        je_lp = bool(k.select_one(".cat_music_lp"))
        je_cd = bool(k.select_one(".cat_music_cd"))
        ima_kupi = bool(k.select_one("a.kupi"))
        a_izaberi = k.select_one("a.izaberi")
        ima_izaberi = bool(a_izaberi)
        dolje = k.select_one(".arhiva-dolje")
        dolje_txt = dolje.get_text(" ", strip=True) if dolje else "NEMA .arhiva-dolje"
        cf = k.select_one("ul.arhiva-cf")
        cf_txt = cf.get_text(" | ", strip=True) if cf else ""
        print(f"#{i:02d} | LP:{je_lp} CD:{je_cd} | kupi:{ima_kupi} izaberi:{ima_izaberi} | CF: {cf_txt} | Dolje: {dolje_txt}", flush=True)
        if je_lp and a_izaberi and len(izaberi_linkovi) < 3:
            izaberi_linkovi.append(a_izaberi.get("href"))

print("\n=== 2. ŠTA PIŠE UNUTRA KADA SE OTVORI LP KARTICA S GUMBOM 'IZABERI'? ===", flush=True)
for link in izaberi_linkovi:
    r_p = sigurni_get(link, timeout=25)
    if r_p and r_p.status_code == 200:
        sp = BeautifulSoup(r_p.text, "lxml")
        nedostupno = bool(sp.select_one(".nedostupno")) or ("nema na stanju" in sp.get_text(" ", strip=True).lower())
        ima_buy_icon = bool(sp.select_one(".product_buy_icon, a.product_cho_link"))
        blok = sp.select_one(".price-cart-block")
        print(f"\nLink: {link}", flush=True)
        print(f" -> Nema na stanju (.nedostupno): {nedostupno} | Ima gumb za kupovinu (.product_cho_link): {ima_buy_icon}", flush=True)
        if blok:
            print(" -> .price-cart-block HTML:", str(blok)[:900], flush=True)
