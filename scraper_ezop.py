from curl_cffi import requests as cffi_requests
import cloudscraper
import requests as std_requests
from bs4 import BeautifulSoup
import urllib3
import socket
import ipaddress
import subprocess
import csv
import os
import re
import time
import html
from urllib.parse import urljoin

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SITE_URL = "https://ezop-antikvarijat.hr"
DOMAIN = "ezop-antikvarijat.hr"
CSV_FILENAME = "ezop_ploce.csv"

HTML_ARCHIVES = [
    "https://ezop-antikvarijat.hr/kategorija/glazba/"
]

API_ENDPOINTS = [
    ("https://ezop-antikvarijat.hr/wp-json/wc/store/products", "?"),
    ("https://ezop-antikvarijat.hr/wp-json/wc/store/v1/products", "?"),
    ("https://ezop-antikvarijat.hr/?rest_route=/wc/store/products", "&")
]

# Službeni Cloudflare IPv4 rasponi (kako bismo ih filtrirali i prepoznali stvarni Origin IP)
CF_NETWORKS = [
    ipaddress.ip_network(cidr) for cidr in [
        "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
        "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
        "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
        "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22"
    ]
]

# Globalni DNS override za direktno spajanje na Origin IP
_ORIG_GETADDRINFO = socket.getaddrinfo
FORCED_ORIGIN_IP = None

def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if FORCED_ORIGIN_IP and host in (DOMAIN, f"www.{DOMAIN}"):
        return _ORIG_GETADDRINFO(FORCED_ORIGIN_IP, port, socket.AF_INET, type, proto, flags)
    return _ORIG_GETADDRINFO(host, port, family, type, proto, flags)

socket.getaddrinfo = _patched_getaddrinfo

def je_li_cloudflare_ip(ip_str):
    try:
        ip_obj = ipaddress.ip_address(ip_str.strip())
        if ip_obj.is_private or ip_obj.is_loopback:
            return True
        return any(ip_obj in net for net in CF_NETWORKS)
    except Exception:
        return True

def dohvati_dns_zapise(ime, tip="A"):
    odgovori = []
    try:
        r = std_requests.get(f"https://dns.google/resolve?name={ime}&type={tip}", timeout=8)
        if r.status_code == 200:
            for ans in r.json().get("Answer", []):
                if "data" in ans:
                    odgovori.append(str(ans["data"]).strip('"'))
    except Exception:
        pass
    return odgovori

def pronadji_kandidate_za_origin_ip():
    print("🕵️ Tražim stvarni Origin IP servera (SPF / MX / Poddomene)...", flush=True)
    kandidati = []

    def dodaj_ip(ip_val, izvor):
        ip_val = ip_val.strip()
        if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", ip_val) and not je_li_cloudflare_ip(ip_val):
            if ip_val not in kandidati:
                kandidati.append(ip_val)
                print(f"   📍 Pronađen ne-Cloudflare IP kandidat [{ip_val}] (Izvor: {izvor})", flush=True)

    # 1. Provjeri TXT (SPF) zapise
    for txt in dohvati_dns_zapise(DOMAIN, "TXT"):
        for m in re.finditer(r"ip4:(\d{1,3}(?:\.\d{1,3}){3})", txt):
            dodaj_ip(m.group(1), "SPF ip4")
        for m in re.finditer(r"(?:a|include):([a-zA-Z0-9.-]+)", txt):
            host_spf = m.group(1)
            if not any(x in host_spf for x in ["google", "outlook", "spf.", "mailchimp", "sendgrid"]):
                for ip_a in dohvati_dns_zapise(host_spf, "A"):
                    dodaj_ip(ip_a, f"SPF host ({host_spf})")

    # 2. Provjeri MX zapise
    for mx in dohvati_dns_zapise(DOMAIN, "MX"):
        mx_host = mx.split()[-1].rstrip(".")
        for ip_a in dohvati_dns_zapise(mx_host, "A"):
            dodaj_ip(ip_a, f"MX ({mx_host})")

    # 3. Provjeri tipične cPanel poddomene
    poddomene = [
        "mail", "cpanel", "webmail", "whm", "ftp", "direct",
        "cpcalendars", "cpcontacts", "autodiscover", "dev", "old", "test"
    ]
    for sub in poddomene:
        puna_sub = f"{sub}.{DOMAIN}"
        for ip_a in dohvati_dns_zapise(puna_sub, "A"):
            dodaj_ip(ip_a, f"Poddomena ({puna_sub})")

    # 4. Ako smo našli cPanel IP (npr. mail je na .11, a web na .10 ili .12), dodaj +/-2 susjedne IP adrese
    osnovni = list(kandidati)
    for ip_baza in osnovni:
        dijelovi = ip_baza.split(".")
        zadnji = int(dijelovi[3])
        for pomak in [-2, -1, 1, 2]:
            novi_zadnji = zadnji + pomak
            if 1 <= novi_zadnji <= 254:
                susjed = f"{dijelovi[0]}.{dijelovi[1]}.{dijelovi[2]}.{novi_zadnji}"
                if susjed not in kandidati and not je_li_cloudflare_ip(susjed):
                    kandidati.append(susjed)

    if not kandidati:
        print("   ℹ️ Nema izloženih Origin IP adresa u javnom DNS-u.", flush=True)
    return kandidati

def postavi_hosts_override(ip_adresa):
    """Upisuje Origin IP u /etc/hosts kako bi i libcurl (curl_cffi) zaobišao Cloudflare."""
    linija = f"\n{ip_adresa} {DOMAIN} www.{DOMAIN}\n"
    try:
        subprocess.run(["sudo", "sh", "-c", f"echo '{linija}' >> /etc/hosts"], check=False, timeout=5)
    except Exception:
        pass

def normaliziraj_link_proizvoda(href):
    if not href:
        return ""
    puni = urljoin(SITE_URL, str(href).strip())
    return puni.split('?')[0].rstrip('/') + '/'

def pretvori_ocjenu(ocjena_str):
    if not ocjena_str:
        return ""
    tekst = str(ocjena_str).strip()
    tekst_upper = tekst.upper()
    if tekst_upper in ["M", "NM", "VG+", "VG", "G+", "G", "F", "P", "NOVO"]:
        return tekst_upper
    if "RABLJENO" in tekst_upper:
        return "VG+"
    try:
        ocjena = int(float(tekst))
        mape = {10: "M", 9: "NM", 8: "VG+", 7: "VG", 6: "G+", 5: "G"}
        return mape.get(ocjena, "VG")
    except Exception:
        return tekst_upper

def ocisti_url_slike(raw_url, is_srcset=False):
    if not raw_url:
        return ""
    val = html.unescape(str(raw_url)).strip()
    val_lower = val.lower()

    if val_lower.startswith('data:image') or any(x in val_lower for x in ['placeholder', 'blank.', 'spinner', 'loader', '.svg', '1x1', 'emoji', 'gravatar']):
        return ""

    if is_srcset:
        kandidati = re.split(r'\s+\d+[wx]\s*,?\s*', val)
        kandidati = [k.strip().rstrip(',') for k in kandidati if k.strip()]
        if not kandidati:
            return ""
        val = kandidati[0]
        if ' ' in val and re.search(r'\s+\d+[wx]$', val):
            val = re.sub(r'\s+\d+[wx]$', '', val).strip()

    if val.startswith('//'):
        val = 'https:' + val
    elif val.startswith('/'):
        val = urljoin(SITE_URL, val)

    if not val.startswith('http'):
        return ""

    return val.replace(' ', '%20')

def izvuci_sliku_iz_elementa(el):
    if not el:
        return ""

    imgs = [el] if getattr(el, 'name', None) == 'img' else el.find_all('img')
    lazy_attrs = [
        'data-lazy-src', 'data-src', 'data-original', 'data-large_image',
        'data-opt-src', 'nitro-lazy-src', 'data-breeze', 'data-wood-src',
        'data-src-img', 'data-echo', 'src'
    ]
    srcset_attrs = ['data-lazy-srcset', 'data-srcset', 'srcset']

    for img in imgs:
        for attr in lazy_attrs:
            url = ocisti_url_slike(img.get(attr, ''), is_srcset=False)
            if url:
                return url
        for attr in srcset_attrs:
            url = ocisti_url_slike(img.get(attr, ''), is_srcset=True)
            if url:
                return url
        for attr_name, attr_val in img.attrs.items():
            if isinstance(attr_val, str) and '/wp-content/uploads/' in attr_val:
                url = ocisti_url_slike(attr_val, is_srcset=('srcset' in attr_name.lower()))
                if url:
                    return url

    for source in el.find_all('source'):
        for attr in ['data-srcset', 'srcset']:
            url = ocisti_url_slike(source.get(attr, ''), is_srcset=True)
            if url:
                return url
        url = ocisti_url_slike(source.get('src', ''), is_srcset=False)
        if url:
            return url

    raw_html = str(el)
    for m in re.finditer(r'<img[^>]+(?:data-lazy-src|data-src|src)=["\']([^"\']+)["\']', raw_html, re.IGNORECASE):
        url = ocisti_url_slike(m.group(1), is_srcset=False)
        if url:
            return url

    for m in re.finditer(r'background-image\s*:\s*url\(["\']?([^"\')]+)["\']?\)', raw_html, re.IGNORECASE):
        url = ocisti_url_slike(m.group(1), is_srcset=False)
        if url:
            return url

    upload_match = re.search(r'(https?://[^"\'\s<>)]+/wp-content/uploads/[^"\'\s<>)]+\.(?:jpg|jpeg|png|webp))', raw_html, re.IGNORECASE)
    if upload_match:
        url = ocisti_url_slike(upload_match.group(1), is_srcset=False)
        if url:
            return url

    return ""

def broj_razlicitih_proizvoda_u_elementu(el):
    linkovi = set()
    for a in el.find_all('a', href=True):
        h = a['href'].strip()
        if '/proizvod/' in h and '?add-to-cart=' not in h:
            linkovi.add(normaliziraj_link_proizvoda(h))
            if len(linkovi) > 1:
                return len(linkovi)
    return len(linkovi)

def pronadji_puni_okvir_kartice(start_el):
    kandidat = start_el
    for _ in range(7):
        roditelj = kandidat.parent
        if not roditelj or getattr(roditelj, 'name', None) in ['body', 'main', 'html', '[document]']:
            break
        if broj_razlicitih_proizvoda_u_elementu(roditelj) == 1:
            kandidat = roditelj
        else:
            break
    return kandidat

def izvuci_cijenu(tekst):
    svi_brojevi = re.findall(r'(\d{1,4}[.,]\d{2})\s*€', tekst)
    if not svi_brojevi:
        svi_brojevi = re.findall(r'(\d{1,4}[.,]\d{2})', tekst)
    if svi_brojevi:
        dijelovi = svi_brojevi[-1].replace(',', '.').split('.')
        if len(dijelovi) == 2:
            try:
                val = float(f"{dijelovi[0]}.{dijelovi[1]}")
                if val > 0:
                    return f"{val:.2f}"
            except Exception:
                pass
    return "0.00"

class EzopSmartTransport:
    def __init__(self):
        self.mode = None # 'origin', 'tor', 'proxy', 'direct'
        self.active_proxy = None
        self.cffi_s = cffi_requests.Session(impersonate="chrome120", verify=False)
        self.std_s = std_requests.Session()
        self.std_s.verify = False
        self.std_s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
            "Accept-Language": "hr-HR,hr;q=0.9,en-US;q=0.8,en;q=0.7"
        })

    def inicijaliziraj_kanal(self):
        global FORCED_ORIGIN_IP
        test_api = "https://ezop-antikvarijat.hr/wp-json/wc/store/products?page=1&per_page=5"
        test_html = "https://ezop-antikvarijat.hr/kategorija/glazba/"

        # 1. PROVJERA DIREKTNOG PRISTUPA
        try:
            r = self.cffi_s.get(test_api, timeout=12)
            if r.status_code == 200 and r.text.strip().startswith('['):
                print("✅ Direktni pristup radi!", flush=True)
                self.mode = 'direct'
                return True
        except Exception:
            pass

        # 2. RAZINA 1: PROBOJ PREKO ORIGIN IP ADRESE (ZAOBILAŽENJE CLOUDFLAREA)
        kandidati = pronadji_kandidate_za_origin_ip()
        for ip_kand in kandidati[:12]:
            FORCED_ORIGIN_IP = ip_kand
            try:
                r = self.std_s.get(test_api, timeout=8)
                if r.status_code == 200 and r.text.strip().startswith('['):
                    print(f"🔓 BINGO! Cloudflare zaobiđen direktno preko Origin IP servera: {ip_kand} (API radi!)", flush=True)
                    postavi_hosts_override(ip_kand)
                    self.mode = 'origin'
                    return True
                r_html = self.std_s.get(test_html, timeout=8)
                if r_html.status_code == 200 and ('/proizvod/' in r_html.text or 'woocommerce' in r_html.text.lower()):
                    print(f"🔓 BINGO! Cloudflare zaobiđen direktno preko Origin IP servera: {ip_kand} (HTML radi!)", flush=True)
                    postavi_hosts_override(ip_kand)
                    self.mode = 'origin'
                    return True
            except Exception:
                pass
        FORCED_ORIGIN_IP = None

        # 3. RAZINA 2: TOR REGIONALNI TUNEL ({hr}, {si}, {at}, {hu}, {ba}, {rs})
        if self.pokreni_tor_regionalni_tunel(test_api, test_html):
            return True

        # 4. RAZINA 3: REGIONALNI JAVNI PROXYJI (HR/SI/AT/HU/DE/CZ/SK)
        if self.pronadji_regionalni_proxy(test_api, test_html):
            return True

        print("❌ Svi kanali za probijanje Cloudflare Geo-blokade su iscrpljeni.", flush=True)
        return False

    def pokreni_tor_regionalni_tunel(self, test_api, test_html):
        print("\n🌐 Podižem Tor regionalni tunel (Hrvatska i susjedne zemlje)...", flush=True)
        try:
            subprocess.run(["sudo", "apt-get", "update", "-qq"], check=False, timeout=45)
            subprocess.run(["sudo", "apt-get", "install", "-y", "-qq", "tor"], check=False, timeout=60)
            torrc = (
                "SocksPort 9050\n"
                "ExitNodes {hr},{si},{ba},{rs},{hu},{at},{me},{mk},{sk},{cz},{ro},{bg},{it},{de}\n"
                "StrictNodes 1\n"
            )
            subprocess.run(["sudo", "sh", "-c", f"echo '{torrc}' > /etc/tor/torrc"], check=False, timeout=5)
            subprocess.run(["sudo", "service", "tor", "restart"], check=False, timeout=15)
            time.sleep(8)

            tor_proxy = "socks5h://127.0.0.1:9050"
            for krug in range(1, 4):
                try:
                    s_tor = cffi_requests.Session(impersonate="chrome120", proxies={"http": tor_proxy, "https": tor_proxy}, verify=False)
                    ip_info = s_tor.get("https://ipinfo.io/json", timeout=12).json()
                    print(f"   🔄 Tor krug {krug}: Izlazna IP {ip_info.get('ip')} ({ip_info.get('country')})", flush=True)

                    r = s_tor.get(test_api, timeout=20)
                    if r.status_code == 200 and r.text.strip().startswith('['):
                        print("🔓 BINGO! Ezop API probijen kroz Tor regionalni tunel!", flush=True)
                        self.cffi_s = s_tor
                        self.active_proxy = tor_proxy
                        self.mode = 'tor'
                        return True

                    r_h = s_tor.get(test_html, timeout=20)
                    if r_h.status_code == 200 and '/proizvod/' in r_h.text:
                        print("🔓 BINGO! Ezop HTML probijen kroz Tor regionalni tunel!", flush=True)
                        self.cffi_s = s_tor
                        self.active_proxy = tor_proxy
                        self.mode = 'tor'
                        return True
                except Exception as e:
                    print(f"   ⚠️ Tor krug {krug} nije prošao: {e}", flush=True)

                subprocess.run(["sudo", "service", "tor", "reload"], check=False, timeout=10)
                time.sleep(5)
        except Exception as e:
            print(f"⚠️ Greška pri pokretanju Tora: {e}", flush=True)
        return False

    def pronadji_regionalni_proxy(self, test_api, test_html):
        print("\n🌍 Tražim otvoreni europski/regionalni proxy...", flush=True)
        proxy_lista = []
        izvori = [
            "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=HR,SI,BA,RS,HU,AT,DE,CZ,SK,IT,PL&ssl=all&anonymity=all",
            "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5&timeout=5000&country=HR,SI,BA,RS,HU,AT,DE,CZ,SK,IT,PL"
        ]
        for url_izvora in izvori:
            try:
                r = std_requests.get(url_izvora, timeout=10)
                if r.status_code == 200:
                    proto = "socks5" if "socks5" in url_izvora else "http"
                    for linija in r.text.splitlines():
                        linija = linija.strip()
                        if ":" in linija:
                            proxy_lista.append(f"{proto}://{linija}")
            except Exception:
                pass

        print(f"   🔎 Pronađeno {len(proxy_lista)} regionalnih proxy kandidata. Testiram...", flush=True)
        for p_url in proxy_lista[:35]:
            try:
                s_p = cffi_requests.Session(impersonate="chrome120", proxies={"http": p_url, "https": p_url}, verify=False)
                r = s_p.get(test_api, timeout=8)
                if r.status_code == 200 and r.text.strip().startswith('['):
                    print(f"🔓 BINGO! Проbijeno preko regionalnog proxyja: {p_url}", flush=True)
                    self.cffi_s = s_p
                    self.active_proxy = p_url
                    self.mode = 'proxy'
                    return True
                r_h = s_p.get(test_html, timeout=8)
                if r_h.status_code == 200 and '/proizvod/' in r_h.text:
                    print(f"🔓 BINGO! HTML probijen preko regionalnog proxyja: {p_url}", flush=True)
                    self.cffi_s = s_p
                    self.active_proxy = p_url
                    self.mode = 'proxy'
                    return True
            except Exception:
                continue
        return False

    def get(self, url, timeout=25):
        if self.mode == 'origin':
            try:
                r = self.std_s.get(url, timeout=timeout)
                if r.status_code in [200, 400, 404]:
                    return r
            except Exception:
                pass
        try:
            r = self.cffi_s.get(url, timeout=timeout)
            if r.status_code in [200, 400, 404]:
                return r
        except Exception:
            pass
        return None

def get_glazba_category_id(transport):
    print("🔍 Tražim ID kategorije 'Glazba'...", flush=True)
    for url in [
        "https://ezop-antikvarijat.hr/wp-json/wc/store/products/categories?per_page=100",
        "https://ezop-antikvarijat.hr/?rest_route=/wc/store/products/categories&per_page=100"
    ]:
        res = transport.get(url, timeout=20)
        if res and res.status_code == 200:
            try:
                podaci = res.json()
                if isinstance(podaci, list):
                    for cat in podaci:
                        slug = cat.get('slug', '').lower()
                        link = cat.get('link', '').lower()
                        if slug == 'glazba' or '/kategorija/glazba' in link:
                            print(f"✅ Pronađena kategorija Glazba (ID: {cat['id']})", flush=True)
                            return cat['id']
            except Exception:
                continue
    return None

def je_li_nevinil(tekst):
    t = tekst.lower()
    zabranjeno = [' cd', '(cd)', 'cd-', 'audio cd', 'kazet', 'kaset', 'knjig', 'strip', 'dvd', 'blu-ray', 'vhs']
    return any(z in t for z in zabranjeno)

def scrape_ezop():
    sve_ploce = {}
    vidjeni_linkovi = set()
    uspjesno_skenirano = False

    if os.path.exists(CSV_FILENAME):
        with open(CSV_FILENAME, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) >= 7:
                    norm_k = normaliziraj_link_proizvoda(row[2])
                    row[2] = norm_k
                    sve_ploce[norm_k] = row
        print(f"📦 Učitano {len(sve_ploce)} postojećih ploča iz memorije.", flush=True)

    transport = EzopSmartTransport()
    if not transport.inicijaliziraj_kanal():
        print("\n⚠️ UPOZORENJE: Nije moguće uspostaviti vezu s Ezopom u ovom pokušaju. Zadržavam svih postojećih " + str(len(sve_ploce)) + " ploča.", flush=True)
        return

    cat_id = get_glazba_category_id(transport)

    # === 1. POKUŠAJ PREKO API-JA ===
    print("\n=== KORAK 1: POKREĆEM EZOP API SKENIRANJE ===", flush=True)
    page = 1
    per_page = 100
    api_uspio = False

    while True:
        print(f"Preuzimam API paket {page}...", end=" ", flush=True)
        res = None
        for base_url, sep in API_ENDPOINTS:
            url = f"{base_url}{sep}category={cat_id}&page={page}&per_page={per_page}" if cat_id else f"{base_url}{sep}page={page}&per_page={per_page}"
            res = transport.get(url, timeout=30)
            if res and res.status_code in [200, 400, 404]:
                break

        if res is None or res.status_code not in [200, 400, 404]:
            print("-> API nedostupan. Prelazim na HTML skeniranje!", flush=True)
            break

        if res.status_code in [400, 404]:
            print("-> Kraj API arhive dostignut.", flush=True)
            if api_uspio:
                uspjesno_skenirano = True
            break

        try:
            data = res.json()
        except Exception:
            print("-> Neispravan JSON. Prelazim na HTML skeniranje!", flush=True)
            break

        if isinstance(data, dict):
            if 'code' in data and api_uspio:
                print(f"-> Kraj API arhive ({data.get('code')}).", flush=True)
                uspjesno_skenirano = True
            break

        if not data or not isinstance(data, list):
            print("-> Prazan paket. Kraj API arhive.", flush=True)
            if api_uspio:
                uspjesno_skenirano = True
            break

        api_uspio = True
        broj_ploca_u_paketu = 0

        for item in data:
            if not item.get('is_in_stock', False):
                continue

            naslov = html.unescape(item.get('name', '')).strip()
            link = normaliziraj_link_proizvoda(item.get('permalink', ''))
            if not naslov or not link:
                continue

            is_vinyl = True if cat_id else False
            nije_vinil_medij = je_li_nevinil(naslov)
            s_omot = ""
            s_medij = ""

            for var in item.get('variations', []):
                for attr in var.get('attributes', []):
                    ime_attr = attr.get('name', '').lower()
                    vrijednost = str(attr.get('value', ''))
                    if je_li_nevinil(vrijednost):
                        nije_vinil_medij = True
                    if 'omot' in ime_attr:
                        s_omot = pretvori_ocjenu(vrijednost)
                        is_vinyl = True
                    elif any(k in ime_attr for k in ['medij', 'ploč', 'ploc', 'stanje']):
                        s_medij = pretvori_ocjenu(vrijednost)
                        is_vinyl = True

            for attr in item.get('attributes', []):
                ime_attr = attr.get('name', '').lower()
                terms = attr.get('terms', [])
                vrijednost = str(terms[0].get('name', '')) if terms else ''
                if je_li_nevinil(vrijednost):
                    nije_vinil_medij = True
                if 'omot' in ime_attr and not s_omot:
                    s_omot = pretvori_ocjenu(vrijednost)
                    is_vinyl = True
                elif any(k in ime_attr for k in ['medij', 'ploč', 'ploc', 'stanje']) and not s_medij:
                    s_medij = pretvori_ocjenu(vrijednost)
                    is_vinyl = True

            for k in item.get('categories', []):
                slug = k.get('slug', '').lower()
                name_kat = k.get('name', '').lower()
                link_kat = k.get('link', '').lower()
                sve_kat = f"{slug} {name_kat} {link_kat}"
                if any(w in sve_kat for w in ['cd', 'kazet', 'kaset', 'strip', 'knjig']):
                    nije_vinil_medij = True
                if any(w in sve_kat for w in ['gramofonske', 'ploce', 'ploče', 'vinil', 'vinyl', 'lp', 'singl', 'glazba']):
                    is_vinyl = True

            if nije_vinil_medij or not is_vinyl:
                continue

            if not s_medij:
                s_medij = "VG+"
            if not s_omot:
                s_omot = s_medij

            prices = item.get('prices', {})
            raw_price = prices.get('price', '0')
            minor_unit = prices.get('currency_minor_unit', 2)
            try:
                price_val = float(raw_price) / (10 ** minor_unit)
                cijena = f"{price_val:.2f}"
            except Exception:
                cijena = "0.00"

            if cijena == "0.00":
                continue

            images = item.get('images', [])
            slika_url = ocisti_url_slike(images[0].get('src', ''), is_srcset=False) if images else ""
            if not slika_url and link in sve_ploce and len(sve_ploce[link]) >= 4:
                slika_url = ocisti_url_slike(sve_ploce[link][3], is_srcset=False)

            sve_ploce[link] = [naslov, cijena, link, slika_url, s_medij, s_omot, "Vinil"]
            vidjeni_linkovi.add(link)
            broj_ploca_u_paketu += 1

        print(f"-> Pronađeno ploča: {broj_ploca_u_paketu}", flush=True)
        page += 1
        time.sleep(0.5)

    # === 2. HTML FALLBACK AKO JE API VRATIO PREMALO PLOČA ===
    if not uspjesno_skenirano or len(vidjeni_linkovi) < 20:
        print("\n=== KORAK 2: POKREĆEM HTML SKENIRANJE EZOP ARHIVE ===", flush=True)
        for base_url in HTML_ARCHIVES:
            page = 1
            praznih_zaredom = 0
            while True:
                url = base_url if page == 1 else f"{base_url.rstrip('/')}/page/{page}/"
                print(f"📄 HTML Stranica {page}...", end=" ", flush=True)

                res = transport.get(url, timeout=25)
                if not res:
                    print("-> Greška spajanja.", flush=True)
                    break
                if res.status_code == 404:
                    print("-> Kraj arhive (404).", flush=True)
                    if page > 1:
                        uspjesno_skenirano = True
                    break
                if res.status_code != 200:
                    print(f"-> Status {res.status_code}", flush=True)
                    break

                soup = BeautifulSoup(res.text, 'lxml')
                kartice = soup.select('li.product, div.product, article.product, .type-product')
                if not kartice:
                    vidjeni_okviri = set()
                    for a in soup.select('a[href*="/proizvod/"]'):
                        if '?add-to-cart=' in a.get('href', ''):
                            continue
                        okvir = pronadji_puni_okvir_kartice(a)
                        if okvir and id(okvir) not in vidjeni_okviri:
                            vidjeni_okviri.add(id(okvir))
                            kartice.append(okvir)

                if not kartice:
                    print("-> Nema više proizvoda. Kraj arhive.", flush=True)
                    if page > 1:
                        uspjesno_skenirano = True
                    break

                dodano_str = 0
                sa_slikom = 0
                vidjeni_na_str = set()

                for k in kartice:
                    puni_k = pronadji_puni_okvir_kartice(k)
                    klase = " ".join((k.get('class', []) or []) + (puni_k.get('class', []) or [])).lower()
                    tekst_k = puni_k.get_text(" ", strip=True)
                    if 'outofstock' in klase or 'rasprodano' in tekst_k.lower():
                        continue

                    link = ""
                    for a in puni_k.find_all('a', href=True):
                        h = a['href'].strip()
                        if '/proizvod/' in h and '?add-to-cart=' not in h:
                            link = normaliziraj_link_proizvoda(h)
                            break
                    if not link or link in vidjeni_na_str:
                        continue

                    naslov_el = puni_k.select_one('.woocommerce-loop-product__title, .product-title, h2, h3, h4')
                    naslov = html.unescape(naslov_el.get_text(" ", strip=True)).strip() if naslov_el else ""
                    if not naslov or je_li_nevinil(naslov):
                        continue

                    cijena_el = puni_k.select_one('.price ins, .price')
                    cijena = izvuci_cijenu(cijena_el.get_text(" ", strip=True) if cijena_el else tekst_k)
                    if cijena == "0.00":
                        continue

                    slika_url = izvuci_sliku_iz_elementa(puni_k)
                    if not slika_url and link in sve_ploce and len(sve_ploce[link]) >= 4:
                        slika_url = ocisti_url_slike(sve_ploce[link][3], is_srcset=False)

                    s_medij = "VG+"
                    s_omot = "VG+"
                    if link in sve_ploce and len(sve_ploce[link]) >= 6:
                        s_medij = sve_ploce[link][4] or "VG+"
                        s_omot = sve_ploce[link][5] or "VG+"

                    if slika_url:
                        sa_slikom += 1

                    vidjeni_na_str.add(link)
                    sve_ploce[link] = [naslov, cijena, link, slika_url, s_medij, s_omot, "Vinil"]
                    vidjeni_linkovi.add(link)
                    dodano_str += 1

                if dodano_str == 0:
                    praznih_zaredom += 1
                    print("-> 0 na zalihi na ovoj stranici.", flush=True)
                    if praznih_zaredom >= 5:
                        uspjesno_skenirano = True
                        break
                else:
                    praznih_zaredom = 0
                    print(f"-> Pronađeno {dodano_str} vinila (Sa slikom: {sa_slikom}/{dodano_str}).", flush=True)

                page += 1
                time.sleep(0.6)

    if uspjesno_skenirano and len(vidjeni_linkovi) > 50:
        pocetni_broj = len(sve_ploce)
        sve_ploce = {k: v for k, v in sve_ploce.items() if k in vidjeni_linkovi}
        obrisano = pocetni_broj - len(sve_ploce)
        print(f"\n🧹 Analiza završena. Obrisano {obrisano} prodanih ploča.", flush=True)
    else:
        print(f"\n⚠️ UPOZORENJE: Pronađeno {len(vidjeni_linkovi)} ploča. Preskačem brisanje starih ploča radi sigurnosti baze.", flush=True)

    if sve_ploce:
        with open(CSV_FILENAME, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Naslov', 'Cijena', 'URL_Proizvoda', 'URL_Slike', 'Stanje_Medija', 'Stanje_Omota', 'Tip_Artikla'])
            writer.writerows(sve_ploce.values())
        ukupno_slika = sum(1 for v in sve_ploce.values() if len(v) >= 4 and v[3])
        print(f"🎉 Završeno. U bazi je sada ukupno {len(sve_ploce)} ažurnih ploča (Sa slikom: {ukupno_slika}).", flush=True)

if __name__ == "__main__":
    scrape_ezop()
