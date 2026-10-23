import requests as std_requests
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor
import urllib3
import socket
import subprocess
import csv
import os
import re
import time
import html
from urllib.parse import urljoin, unquote

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SITE_URL = "https://ezop-antikvarijat.hr"
DOMAIN = "ezop-antikvarijat.hr"
CSV_FILENAME = "ezop_ploce.csv"

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

def normaliziraj_link_proizvoda(href):
    if not href:
        return ""
    puni = urljoin(SITE_URL, str(href).strip())
    return puni.split('?')[0].rstrip('/') + '/'

def pretvori_ocjenu(ocjena_str):
    if not ocjena_str:
        return ""
    tekst = str(ocjena_str).strip().strip('"').strip("'")
    tekst_upper = tekst.upper()

    if "NOV" in tekst_upper or tekst_upper == "SS":
        return "Novo"
    if tekst_upper in ["M", "NM", "M-", "EX+", "EX", "EX-", "VG+", "VG", "VG-", "G+", "G", "F", "P"]:
        return "NM" if tekst_upper == "M-" else tekst_upper
    if "RABLJENO" in tekst_upper:
        return "Rabljeno"

    # Ako piše npr. "9/10" ili samo broj "9"
    m_broj = re.search(r'\b(10|[1-9])\b', tekst)
    if m_broj:
        ocjena = int(m_broj.group(1))
        mape = {
            10: "M",
            9: "NM",
            8: "VG+",
            7: "VG",
            6: "G+",
            5: "G",
            4: "F",
            3: "P",
            2: "P",
            1: "P"
        }
        return mape.get(ocjena, "VG")

    return ""

def izvuci_tekstualnu_ocjenu_kosa_crta(tekst):
    """Prepoznaje tekstualni zapis tipa VG/G, VG+/VG, NM/EX ispod naslova ili u opisu."""
    if not tekst:
        return "", ""
    m = re.search(
        r'\b(M|NM|M-|EX\+|EX-|EX|VG\+|VG-|VG|G\+|G|F|P)\s*/\s*(M|NM|M-|EX\+|EX-|EX|VG\+|VG-|VG|G\+|G|F|P)\b',
        tekst.upper()
    )
    if m:
        s_med = pretvori_ocjenu(m.group(1))
        s_om = pretvori_ocjenu(m.group(2))
        return s_med, s_om
    return "", ""

def ocisti_url_slike(raw_url, is_srcset=False):
    if not raw_url:
        return ""
    val = html.unescape(str(raw_url)).strip()
    val_lower = val.lower()

    # Ignoriraj Ezopove sistemske ikonice, okvire za LP/CD i placeholdere
    zabranjene_slike = [
        'data:image', 'placeholder', 'blank.', 'spinner', 'loader', '.svg',
        '1x1', 'emoji', 'gravatar', 'glazba-lp', 'glazba-cd', 'novo-1',
        'cart_kupi', '/themes/ezop/', 'zastava'
    ]
    if any(x in val_lower for x in zabranjene_slike):
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

def izvuci_sliku_iz_ezop_kartice(k):
    if not k:
        return ""

    # 1. Prvo tražimo punu rezoluciju omota (wp-post-image) ili woocommerce_thumbnail
    prioritetne = k.select('img.wp-post-image, img.attachment-woocommerce_thumbnail')
    ostale = [img for img in k.find_all('img') if img not in prioritetne]

    for img in prioritetne + ostale:
        klase_img = " ".join(img.get('class', [])).lower()
        if any(z in klase_img for z in ['glazba-bg', 'arhiva-pecat', 'zastava', 'product_buy_icon']):
            continue

        for attr in ['data-lazy-src', 'data-src', 'src']:
            url = ocisti_url_slike(img.get(attr, ''), is_srcset=False)
            if url:
                return url
        for attr in ['data-lazy-srcset', 'data-srcset', 'srcset']:
            url = ocisti_url_slike(img.get(attr, ''), is_srcset=True)
            if url:
                return url

    # 2. Provjera <noscript> oznaka u sirovom HTML-u kartice
    raw_html = str(k)
    for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', raw_html, re.IGNORECASE):
        url = ocisti_url_slike(m.group(1), is_srcset=False)
        if url:
            return url

    return ""

def izvuci_cijenu_iz_elementa(price_el):
    if not price_el:
        return "0.00"
    # Spajamo bez razmaka jer Ezop piše <span class="big">28</span>,<span class="small_price">50</span>
    tekst_spojeno = price_el.get_text("", strip=True).replace(".", "")
    parovi = re.findall(r'(\d{1,4}),(\d{2})', tekst_spojeno)
    if parovi:
        # Uzimamo prvu cijenu (ili jedinu cijenu)
        try:
            val = float(f"{parovi[0][0]}.{parovi[0][1]}")
            if val > 0:
                return f"{val:.2f}"
        except Exception:
            pass
    return "0.00"

def kreiraj_sesiju(koristi_tor=False):
    s = std_requests.Session()
    s.verify = False
    adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
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

def sigurni_get(url, timeout=30):
    global session, tor_aktivan
    for pokusaj in range(1, 4):
        try:
            r = session.get(url, timeout=timeout)
            if r.status_code in [200, 404]:
                return r
            if r.status_code in [522, 403, 503]:
                if not tor_aktivan:
                    podigni_tor()
                    session = kreiraj_sesiju(koristi_tor=True)
                    tor_aktivan = True
                else:
                    time.sleep(3)
        except Exception:
            if pokusaj == 2 and not tor_aktivan:
                podigni_tor()
                session = kreiraj_sesiju(koristi_tor=True)
                tor_aktivan = True
            time.sleep(2)
    return None

def provjeri_izaberi_proizvod(stavka):
    """Otvara pojedinačnu stranicu za kartice s gumbom 'Izaberi' i provjerava stanje/ocjene."""
    link, naslov, cijena, slika_url, tip_artikla = stavka
    r = sigurni_get(link, timeout=25)
    if not r or r.status_code != 200:
        return None

    sp = BeautifulSoup(r.text, "lxml")
    blok = sp.select_one(".price-cart-block") or sp

    # 1. Sigurnosna provjera je li stvarno na stanju
    ima_nedostupno = bool(blok.select_one(".nedostupno")) or ("trenutno nema na stanju" in blok.get_text(" ", strip=True).lower())
    cho_link = blok.select_one("a.product_cho_link")
    if ima_nedostupno and not cho_link:
        return None

    s_medij = ""
    s_omot = ""

    # 2. Provjera <div class="product_mid_part"><ul class="product_small_info"> (Slika 3)
    for li in blok.select(".product_small_info li"):
        t = li.get_text(" ", strip=True)
        t_low = t.lower()
        if "omot:" in t_low and not s_omot:
            s_omot = pretvori_ocjenu(t.split(":", 1)[1])
        elif "medij:" in t_low and not s_medij:
            s_medij = pretvori_ocjenu(t.split(":", 1)[1])
        elif "stanje:" in t_low and not s_medij:
            s_medij = pretvori_ocjenu(t.split(":", 1)[1])

    # 3. Provjera <div class="single-product-cf"><p class="info"> (Slika 1 i 2)
    for p_info in blok.select(".single-product-cf p.info"):
        t = p_info.get_text(" ", strip=True)
        t_low = t.lower()
        if "stanje omota:" in t_low and not s_omot:
            s_omot = pretvori_ocjenu(t.split(":", 1)[1])
        elif "stanje medija:" in t_low and not s_medij:
            s_medij = pretvori_ocjenu(t.split(":", 1)[1])
        elif "stanje:" in t_low and not s_medij:
            s_medij = pretvori_ocjenu(t.split(":", 1)[1])

    # 4. Provjera URL parametara u gumbu za kupovinu (attribute_pa_omot / attribute_pa_medij)
    if cho_link and cho_link.get("href"):
        href_dec = unquote(cho_link["href"])
        m_om = re.search(r'attribute_pa_omot=([^&#]+)', href_dec)
        m_me = re.search(r'attribute_pa_medij=([^&#]+)', href_dec)
        m_st = re.search(r'attribute_pa_stanje=([^&#]+)', href_dec)
        if m_om and not s_omot:
            s_omot = pretvori_ocjenu(m_om.group(1))
        if m_me and not s_medij:
            s_medij = pretvori_ocjenu(m_me.group(1))
        if m_st and not s_medij:
            s_medij = pretvori_ocjenu(m_st.group(1))

    # 5. Provjera tekstualne ocjene s kosom crtom (npr. VG/G) ispod naslova
    if not s_medij and not s_omot:
        m_med, m_om = izvuci_tekstualnu_ocjenu_kosa_crta(blok.get_text(" ", strip=True))
        if m_med:
            s_medij = m_med
            s_omot = m_om

    if not s_medij and s_omot:
        s_medij = s_omot
    if not s_omot and s_medij:
        s_omot = s_medij
    if not s_medij:
        s_medij = "Rabljeno"
        s_omot = "Rabljeno"

    # Ako na arhivi nije bila slika, izvuci sa stranice proizvoda
    if not slika_url:
        gal = sp.select_one(".woocommerce-product-gallery")
        if gal:
            slika_url = izvuci_sliku_iz_ezop_kartice(gal)

    return (link, [naslov, cijena, link, slika_url, s_medij, s_omot, tip_artikla])

def parsiraj_ezop_stranicu(soup, sve_ploce):
    direktne_stavke = []
    izaberi_za_provjeru = []

    kartice = soup.select("div.product, li.product")
    vidjeni_na_str = set()

    for k in kartice:
        # 1. Preskoči CD-ove po klasi ikone
        if k.select_one(".cat_music_cd, .home_music_cd"):
            continue

        # 2. Provjeri polja u <ul class="arhiva-cf">
        medij_vrsta = ""
        s_omot = ""
        s_medij = ""

        for li in k.select("ul.arhiva-cf li"):
            t = li.get_text(" ", strip=True)
            t_low = t.lower()
            if "medij:" in t_low and "stanje" not in t_low:
                medij_vrsta = t.split(":", 1)[1].strip()
            elif "stanje omota:" in t_low:
                s_omot = pretvori_ocjenu(t.split(":", 1)[1])
            elif "stanje medija:" in t_low:
                s_medij = pretvori_ocjenu(t.split(":", 1)[1])
            elif "stanje:" in t_low:
                s_medij = pretvori_ocjenu(t.split(":", 1)[1])
                s_omot = s_medij

        medij_low = medij_vrsta.lower()
        # Strogi filter: propuštamo SAMO gramofonske ploče (LP) i gramofonske singlice (7")
        if any(z in medij_low for z in ["cd", "kazet", "kaset", "dvd", "vhs", "knjig", "strip"]):
            continue

        je_lp_ikona = bool(k.select_one(".cat_music_lp, .home_music_lp"))
        if "singl" in medij_low or '7"' in medij_low:
            tip_artikla = 'Singl 7"'
        elif "gramofonsk" in medij_low or "ploč" in medij_low or "ploc" in medij_low or je_lp_ikona:
            tip_artikla = "LP"
        else:
            continue

        # 3. Provjera dostupnosti (mora imati gumb 'kupi' ili 'izaberi', bez oznake '.nedostupno')
        if k.select_one(".nedostupno"):
            continue
        a_kupi = k.select_one("a.kupi")
        a_izaberi = k.select_one("a.izaberi")
        if not a_kupi and not a_izaberi:
            continue

        # 4. Izvlačenje linka
        a_link = k.select_one("a.prod_link, .top a") or a_izaberi
        if not a_link or not a_link.get("href"):
            continue
        link = normaliziraj_link_proizvoda(a_link["href"])
        if not link or link in vidjeni_na_str:
            continue

        # 5. Izvlačenje punog naslova (Izvođač - Naslov albuma)
        izvodjac_el = k.select_one("h2.woocommerce-loop-product__title")
        album_el = k.select_one("p.product_author_black")
        izvodjac = html.unescape(izvodjac_el.get_text(" ", strip=True)).strip() if izvodjac_el else ""
        album = html.unescape(album_el.get_text(" ", strip=True)).strip() if album_el else ""

        if izvodjac and album and izvodjac.lower() != album.lower():
            naslov = f"{izvodjac} - {album}"
        else:
            naslov = izvodjac or album
        if not naslov:
            continue

        # 6. Cijena
        price_el = k.select_one(".my-custom-field-knjizara.price, .price")
        cijena = izvuci_cijenu_iz_elementa(price_el)
        if cijena == "0.00":
            continue

        # 7. Slika omota
        slika_url = izvuci_sliku_iz_ezop_kartice(k)
        if not slika_url and link in sve_ploce and len(sve_ploce[link]) >= 4:
            slika_url = ocisti_url_slike(sve_ploce[link][3], is_srcset=False)

        # 8. Provjera pečata 'Novo'
        if k.select_one('img.arhiva-pecat[title*="Novo"], img.arhiva-pecat[alt*="Novo"]'):
            s_medij = "Novo"
            s_omot = "Novo"

        vidjeni_na_str.add(link)

        # Ako kartica ima direktni gumb 'Dodaj u košaricu' (a.kupi), 100% je na stanju i sve imamo odmah!
        if a_kupi:
            if (not s_omot or not s_medij) and a_kupi.get("href"):
                href_dec = unquote(a_kupi["href"])
                m_om = re.search(r'attribute_pa_omot=([^&#]+)', href_dec)
                m_me = re.search(r'attribute_pa_medij=([^&#]+)', href_dec)
                if m_om and not s_omot:
                    s_omot = pretvori_ocjenu(m_om.group(1))
                if m_me and not s_medij:
                    s_medij = pretvori_ocjenu(m_me.group(1))

            if not s_medij and not s_omot:
                m_med, m_om = izvuci_tekstualnu_ocjenu_kosa_crta(k.get_text(" ", strip=True))
                if m_med:
                    s_medij, s_omot = m_med, m_om

            if not s_medij and s_omot:
                s_medij = s_omot
            if not s_omot and s_medij:
                s_omot = s_medij
            if not s_medij:
                s_medij = "Rabljeno"
                s_omot = "Rabljeno"

            direktne_stavke.append((link, [naslov, cijena, link, slika_url, s_medij, s_omot, tip_artikla]))
        else:
            # Za 'Izaberi' kartice: ako smo je već ranije provjerili kroz HTML scraper (Tip je 'LP' ili 'Singl 7"'), zadrži ocjene
            if link in sve_ploce and len(sve_ploce[link]) >= 7 and sve_ploce[link][6] in ['LP', 'Singl 7"']:
                stari_medij = sve_ploce[link][4] or "Rabljeno"
                stari_omot = sve_ploce[link][5] or "Rabljeno"
                direktne_stavke.append((link, [naslov, cijena, link, slika_url, stari_medij, stari_omot, tip_artikla]))
            else:
                izaberi_za_provjeru.append((link, naslov, cijena, slika_url, tip_artikla))

    # Paralelno provjeri 'Izaberi' proizvode na ovoj stranici (4 niti)
    if izaberi_za_provjeru:
        with ThreadPoolExecutor(max_workers=4) as executor:
            rezultati = list(executor.map(provjeri_izaberi_proizvod, izaberi_za_provjeru))
        for r_item in rezultati:
            if r_item is not None:
                direktne_stavke.append(r_item)

    return direktne_stavke, len(kartice)

def scrape_ezop_html():
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
        print(f"📦 Učitano {len(sve_ploce)} postojećih zapisa iz CSV-a.", flush=True)

    print("⚡ Zagrijavam direktnu konekciju prema Ezop serveru (116.202.174.206)...", flush=True)
    sigurni_get("https://ezop-antikvarijat.hr/wp-json/wc/store/products/315415", timeout=15)

    base_url = "https://ezop-antikvarijat.hr/kategorija/glazba/"
    page = 1
    max_pages = 500

    print("\n🚀 Pokrećem brzo HTML skeniranje dostupnih vinila na Ezopu...", flush=True)

    while page <= max_pages:
        url = base_url if page == 1 else f"{base_url}page/{page}/"
        t0 = time.time()
        print(f"📄 Stranica {page}...", end=" ", flush=True)

        res = sigurni_get(url, timeout=40)
        if not res:
            print("-> ❌ Prekid veze na stranici. Zaustavljam radi sigurnosti baze.", flush=True)
            break

        if res.status_code == 404:
            print("-> Kraj arhive (404).", flush=True)
            if page > 50:
                uspjesno_skenirano = True
            break

        soup = BeautifulSoup(res.text, "lxml")

        if page == 1:
            brojevi_str = [int(el.get_text(strip=True)) for el in soup.select(".page-numbers") if el.get_text(strip=True).isdigit()]
            if brojevi_str:
                max_pages = max(brojevi_str)
                print(f"[Ukupno stranica u arhivi: {max_pages}]", end=" ", flush=True)

        stavke, ukupno_kartica = parsiraj_ezop_stranicu(soup, sve_ploce)

        if ukupno_kartica == 0:
            print("-> Nema više kartica. Kraj arhive.", flush=True)
            if page > 50:
                uspjesno_skenirano = True
            break

        sa_slikom = 0
        for link, red_podataka in stavke:
            if red_podataka[3]:
                sa_slikom += 1
            sve_ploce[link] = red_podataka
            vidjeni_linkovi.add(link)

        trajanje = time.time() - t0
        print(f"-> +{len(stavke)} vinila (Slika: {sa_slikom}/{len(stavke)}) za {trajanje:.1f}s | Ukupno vinila: {len(vidjeni_linkovi)}", flush=True)

        if page >= max_pages:
            print("-> Dosegnuta zadnja stranica arhive!", flush=True)
            uspjesno_skenirano = True
            break

        page += 1
        time.sleep(0.15)

    # Čišćenje starih rasprodanih ploča, knjiga i CD-ova iz CSV-a
    if uspjesno_skenirano and len(vidjeni_linkovi) > 500:
        pocetni_broj = len(sve_ploce)
        sve_ploce = {k: v for k, v in sve_ploce.items() if k in vidjeni_linkovi}
        obrisano = pocetni_broj - len(sve_ploce)
        print(f"\n🧹 Sinkronizacija završena! Iz CSV-a uklonjeno {obrisano} rasprodanih artikala/knjiga/CD-ova.", flush=True)
    else:
        print(f"\n⚠️ Skeniranje nije došlo do samog kraja (Pronađeno vinila: {len(vidjeni_linkovi)}). Preskačem brisanje starih zapisa.", flush=True)

    if sve_ploce:
        with open(CSV_FILENAME, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Naslov', 'Cijena', 'URL_Proizvoda', 'URL_Slike', 'Stanje_Medija', 'Stanje_Omota', 'Tip_Artikla'])
            writer.writerows(sve_ploce.values())
        ukupno_slika = sum(1 for v in sve_ploce.values() if len(v) >= 4 and v[3])
        print(f"🎉 GOTOVO! U bazi je sada {len(sve_ploce)} stvarno dostupnih Ezop vinila (Sa slikom: {ukupno_slika}).", flush=True)

if __name__ == "__main__":
    scrape_ezop_html()
