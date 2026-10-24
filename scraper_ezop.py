import requests as std_requests
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib3
import socket
import subprocess
import threading
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

ORIGIN_IP = "116.202.174.206"
BROJ_NITI = 6  # Optimalan broj paralelnih konekcija bez izazivanja HTTP 522

_ORIG_GETADDRINFO = socket.getaddrinfo
def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if host in (DOMAIN, f"www.{DOMAIN}"):
        return _ORIG_GETADDRINFO(ORIGIN_IP, port, socket.AF_INET, type, proto, flags)
    return _ORIG_GETADDRINFO(host, port, family, type, proto, flags)

socket.getaddrinfo = _patched_getaddrinfo

_thread_local = threading.local()
_tor_lock = threading.Lock()
tor_aktivan = False

def kreiraj_sesiju(koristi_tor=False):
    s = std_requests.Session()
    s.verify = False
    adapter = HTTPAdapter(pool_connections=BROJ_NITI + 4, pool_maxsize=BROJ_NITI + 4)
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

def dohvati_sesiju():
    global tor_aktivan
    s = getattr(_thread_local, "session", None)
    sesija_tor = getattr(_thread_local, "koristi_tor", False)
    if s is None or sesija_tor != tor_aktivan:
        s = kreiraj_sesiju(koristi_tor=tor_aktivan)
        _thread_local.session = s
        _thread_local.koristi_tor = tor_aktivan
    return s

def podigni_tor():
    global tor_aktivan
    with _tor_lock:
        if tor_aktivan:
            return
        print("\n⚠️ Palim Tor tunel prema Origin IP-u...", flush=True)
        subprocess.run(["sudo", "apt-get", "update", "-qq"], check=False, timeout=45)
        subprocess.run(["sudo", "apt-get", "install", "-y", "-qq", "tor"], check=False, timeout=60)
        subprocess.run(["sudo", "service", "tor", "restart"], check=False, timeout=15)
        time.sleep(5)
        tor_aktivan = True

def sigurni_get(url, timeout=30):
    for pokusaj in range(1, 4):
        try:
            s = dohvati_sesiju()
            r = s.get(url, timeout=timeout)
            if r.status_code in [200, 404]:
                return r
            if r.status_code in [522, 403, 503]:
                if pokusaj == 2 and not tor_aktivan:
                    podigni_tor()
                else:
                    time.sleep(1.5 * pokusaj)
        except Exception:
            if pokusaj == 2 and not tor_aktivan:
                podigni_tor()
            else:
                time.sleep(1.5 * pokusaj)
    return None

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
    if not tekst:
        return "", ""
    m = re.search(
        r'\b(M|NM|M-|EX\+|EX-|EX|VG\+|VG-|VG|G\+|G|F|P)\s*/\s*(M|NM|M-|EX\+|EX-|EX|VG\+|VG-|VG|G\+|G|F|P)\b',
        tekst.upper()
    )
    if m:
        return pretvori_ocjenu(m.group(1)), pretvori_ocjenu(m.group(2))
    return "", ""

def ocisti_url_slike(raw_url, is_srcset=False):
    if not raw_url:
        return ""
    val = html.unescape(str(raw_url)).strip()
    val_lower = val.lower()

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

    raw_html = str(k)
    for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', raw_html, re.IGNORECASE):
        url = ocisti_url_slike(m.group(1), is_srcset=False)
        if url:
            return url

    return ""

def izvuci_cijenu_iz_elementa(price_el):
    if not price_el:
        return "0.00"
    tekst_spojeno = price_el.get_text("", strip=True).replace(".", "")
    parovi = re.findall(r'(\d{1,4}),(\d{2})', tekst_spojeno)
    if parovi:
        try:
            val = float(f"{parovi[0][0]}.{parovi[0][1]}")
            if val > 0:
                return f"{val:.2f}"
        except Exception:
            pass
    return "0.00"

def provjeri_izaberi_proizvod(stavka):
    """Otvara pojedinačnu stranicu samo za NOVE 'Izaberi' artikle kojih još nema u CSV-u."""
    link, naslov, cijena, slika_url, tip_artikla = stavka
    r = sigurni_get(link, timeout=25)
    if not r or r.status_code != 200:
        # Ako je stranica trenutno nedostupna, zadrži artikl s oznakom Rabljeno
        return (link, [naslov, cijena, link, slika_url, "Rabljeno", "Rabljeno", tip_artikla])

    sp = BeautifulSoup(r.text, "lxml")
    blok = sp.select_one(".price-cart-block") or sp

    ima_nedostupno = bool(blok.select_one(".nedostupno")) or ("trenutno nema na stanju" in blok.get_text(" ", strip=True).lower())
    cho_link = blok.select_one("a.product_cho_link")
    if ima_nedostupno and not cho_link:
        return None

    s_medij = ""
    s_omot = ""

    for li in blok.select(".product_small_info li"):
        t = li.get_text(" ", strip=True)
        t_low = t.lower()
        if "omot:" in t_low and not s_omot:
            s_omot = pretvori_ocjenu(t.split(":", 1)[1])
        elif "medij:" in t_low and not s_medij:
            s_medij = pretvori_ocjenu(t.split(":", 1)[1])
        elif "stanje:" in t_low and not s_medij:
            s_medij = pretvori_ocjenu(t.split(":", 1)[1])

    for p_info in blok.select(".single-product-cf p.info"):
        t = p_info.get_text(" ", strip=True)
        t_low = t.lower()
        if "stanje omota:" in t_low and not s_omot:
            s_omot = pretvori_ocjenu(t.split(":", 1)[1])
        elif "stanje medija:" in t_low and not s_medij:
            s_medij = pretvori_ocjenu(t.split(":", 1)[1])
        elif "stanje:" in t_low and not s_medij:
            s_medij = pretvori_ocjenu(t.split(":", 1)[1])

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

    if not s_medij and not s_omot:
        m_med, m_om = izvuci_tekstualnu_ocjenu_kosa_crta(blok.get_text(" ", strip=True))
        if m_med:
            s_medij, s_omot = m_med, m_om

    if not s_medij and s_omot:
        s_medij = s_omot
    if not s_omot and s_medij:
        s_omot = s_medij
    if not s_medij:
        s_medij = "Rabljeno"
        s_omot = "Rabljeno"

    if not slika_url:
        gal = sp.select_one(".woocommerce-product-gallery")
        if gal:
            slika_url = izvuci_sliku_iz_ezop_kartice(gal)

    return (link, [naslov, cijena, link, slika_url, s_medij, s_omot, tip_artikla])

def parsiraj_ezop_stranicu(soup, sve_ploce):
    direktne_stavke = []
    novi_izaberi = []

    kartice = soup.select("div.product, li.product")
    vidjeni_na_str = set()

    for k in kartice:
        if k.select_one(".cat_music_cd, .home_music_cd"):
            continue

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
        if any(z in medij_low for z in ["cd", "kazet", "kaset", "dvd", "vhs", "knjig", "strip"]):
            continue

        je_lp_ikona = bool(k.select_one(".cat_music_lp, .home_music_lp"))
        if "singl" in medij_low or '7"' in medij_low:
            tip_artikla = 'Singl 7"'
        elif "gramofonsk" in medij_low or "ploč" in medij_low or "ploc" in medij_low or je_lp_ikona:
            tip_artikla = "LP"
        else:
            continue

        if k.select_one(".nedostupno"):
            continue
        a_kupi = k.select_one("a.kupi")
        a_izaberi = k.select_one("a.izaberi")
        if not a_kupi and not a_izaberi:
            continue

        a_link = k.select_one("a.prod_link, .top a") or a_izaberi
        if not a_link or not a_link.get("href"):
            continue
        link = normaliziraj_link_proizvoda(a_link["href"])
        if not link or link in vidjeni_na_str:
            continue

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

        price_el = k.select_one(".my-custom-field-knjizara.price, .price")
        cijena = izvuci_cijenu_iz_elementa(price_el)
        if cijena == "0.00":
            continue

        slika_url = izvuci_sliku_iz_ezop_kartice(k)
        if not slika_url and link in sve_ploce and len(sve_ploce[link]) >= 4:
            slika_url = ocisti_url_slike(sve_ploce[link][3], is_srcset=False)

        if k.select_one('img.arhiva-pecat[title*="Novo"], img.arhiva-pecat[alt*="Novo"]'):
            s_medij = "Novo"
            s_omot = "Novo"

        vidjeni_na_str.add(link)

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
            # Ako je 'Izaberi' artikl već u našoj bazi s provjerenim stanjem, ne otvaramo ponovno stranicu
            if link in sve_ploce and len(sve_ploce[link]) >= 7 and sve_ploce[link][6] in ['LP', 'Singl 7"'] and sve_ploce[link][4]:
                stari_medij = sve_ploce[link][4]
                stari_omot = sve_ploce[link][5] or stari_medij
                direktne_stavke.append((link, [naslov, cijena, link, slika_url, stari_medij, stari_omot, tip_artikla]))
            else:
                novi_izaberi.append((link, naslov, cijena, slika_url, tip_artikla))

    return direktne_stavke, novi_izaberi, len(kartice)

def obradi_jednu_stranicu(args):
    page, base_url, sve_ploce = args
    url = base_url if page == 1 else f"{base_url}page/{page}/"
    res = sigurni_get(url, timeout=35)
    if not res or res.status_code != 200:
        return page, False, [], [], 0
    soup = BeautifulSoup(res.text, "lxml")
    direktne, izaberi, br_kartica = parsiraj_ezop_stranicu(soup, sve_ploce)
    return page, True, direktne, izaberi, br_kartica

def scrape_ezop_html():
    t_start = time.time()
    sve_ploce = {}
    vidjeni_linkovi = set()

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

    print(f"⚡ Zagrijavam direktnu konekciju prema Ezop serveru ({ORIGIN_IP})...", flush=True)
    sigurni_get("https://ezop-antikvarijat.hr/wp-json/wc/store/products/315415", timeout=15)

    base_url = "https://ezop-antikvarijat.hr/kategorija/glazba/"
    print("📄 Dohvaćam 1. stranicu i ukupan broj stranica arhive...", flush=True)

    res1 = sigurni_get(base_url, timeout=35)
    if not res1 or res1.status_code != 200:
        print("❌ Neuspješno dohvaćanje 1. stranice arhive. Prekidam.", flush=True)
        return

    soup1 = BeautifulSoup(res1.text, "lxml")
    brojevi_str = [int(el.get_text(strip=True)) for el in soup1.select(".page-numbers") if el.get_text(strip=True).isdigit()]
    max_pages = max(brojevi_str) if brojevi_str else 447

    direktne1, izaberi1, _ = parsiraj_ezop_stranicu(soup1, sve_ploce)
    svi_novi_izaberi = {}

    for link, red in direktne1:
        sve_ploce[link] = red
        vidjeni_linkovi.add(link)
    for st in izaberi1:
        svi_novi_izaberi[st[0]] = st

    print(f"🚀 Pokrećem paralelno skeniranje {max_pages} stranica ({BROJ_NITI} niti istovremeno)...", flush=True)

    neuspjesne_stranice = []
    preostale_stranice = list(range(2, max_pages + 1))
    velicina_bloka = BROJ_NITI * 3  # Ispis napretka svakih 18 stranica

    with ThreadPoolExecutor(max_workers=BROJ_NITI) as executor:
        for i in range(0, len(preostale_stranice), velicina_bloka):
            blok = preostale_stranice[i:i + velicina_bloka]
            t_blok = time.time()
            zadaci = [(p, base_url, sve_ploce) for p in blok]
            rezultati = list(executor.map(obradi_jednu_stranicu, zadaci))

            dodano_u_bloku = 0
            uspjesno_u_bloku = 0

            for page, ok, direktne, izaberi, br_kartica in rezultati:
                if not ok or br_kartica == 0:
                    neuspjesne_stranice.append(page)
                    continue
                uspjesno_u_bloku += 1
                for link, red in direktne:
                    sve_ploce[link] = red
                    vidjeni_linkovi.add(link)
                    dodano_u_bloku += 1
                for st in izaberi:
                    svi_novi_izaberi[st[0]] = st

            print(
                f"⚡ Stranice {blok[0]:03d}-{blok[-1]:03d} ({uspjesno_u_bloku}/{len(blok)} OK) "
                f"za {time.time() - t_blok:.1f}s | +{dodano_u_bloku} izravnih | "
                f"Ukupno u bazi: {len(vidjeni_linkovi)} (+{len(svi_novi_izaberi)} novih Izaberi)",
                flush=True
            )

        # Popravni krug za eventualne stranice koje su prvi put imale timeout
        if neuspjesne_stranice:
            print(f"\n🔄 Ponavljam dohvat za {len(neuspjesne_stranice)} stranica koje su imale timeout...", flush=True)
            popravni_zadaci = [(p, base_url, sve_ploce) for p in neuspjesne_stranice]
            popravni_rezultati = list(executor.map(obradi_jednu_stranicu, popravni_zadaci))
            jos_uvijek_neuspjesne = []

            for page, ok, direktne, izaberi, br_kartica in popravni_rezultati:
                if not ok or br_kartica == 0:
                    jos_uvijek_neuspjesne.append(page)
                    continue
                for link, red in direktne:
                    sve_ploce[link] = red
                    vidjeni_linkovi.add(link)
                for st in izaberi:
                    svi_novi_izaberi[st[0]] = st
            neuspjesne_stranice = jos_uvijek_neuspjesne

        # Paralelna provjera samo NOVIH 'Izaberi' artikala
        if svi_novi_izaberi:
            lista_izaberi = list(svi_novi_izaberi.values())
            print(f"\n🔍 Provjeravam ocjene za {len(lista_izaberi)} novih 'Izaberi' artikala ({BROJ_NITI} niti)...", flush=True)
            rez_izaberi = list(executor.map(provjeri_izaberi_proizvod, lista_izaberi))
            potvrdjeno = 0
            for r_item in rez_izaberi:
                if r_item is not None:
                    link, red = r_item
                    sve_ploce[link] = red
                    vidjeni_linkovi.add(link)
                    potvrdjeno += 1
            print(f"✅ Potvrđeno {potvrdjeno}/{len(lista_izaberi)} novih 'Izaberi' vinila.", flush=True)

    udio_uspjeha = (max_pages - len(neuspjesne_stranice)) / float(max_pages)
    if udio_uspjeha >= 0.96 and len(vidjeni_linkovi) > 1000:
        pocetni_broj = len(sve_ploce)
        sve_ploce = {k: v for k, v in sve_ploce.items() if k in vidjeni_linkovi}
        obrisano = pocetni_broj - len(sve_ploce)
        print(f"\n🧹 Sinkronizacija završena! Uklonjeno {obrisano} prodanih artikala.", flush=True)
    else:
        print(f"\n⚠️ Preskočeno {len(neuspjesne_stranice)} stranica — zadržavam postojeće zapise radi sigurnosti.", flush=True)

    if sve_ploce:
        with open(CSV_FILENAME, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Naslov', 'Cijena', 'URL_Proizvoda', 'URL_Slike', 'Stanje_Medija', 'Stanje_Omota', 'Tip_Artikla'])
            writer.writerows(sve_ploce.values())
        ukupno_slika = sum(1 for v in sve_ploce.values() if len(v) >= 4 and v[3])
        ukupno_min = (time.time() - t_start) / 60.0
        print(f"🎉 GOTOVO za {ukupno_min:.1f} min! U bazi je {len(sve_ploce)} dostupnih Ezop vinila (Sa slikom: {ukupno_slika}).", flush=True)

if __name__ == "__main__":
    scrape_ezop_html()
