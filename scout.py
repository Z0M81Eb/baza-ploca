from curl_cffi import requests as cffi_requests
import cloudscraper
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
import urllib3
import time
import os
import re
import sys

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CILJANI_URL = os.environ.get("TARGET_URL", "").strip()

os.makedirs("logs", exist_ok=True)
LOG_FILE = "logs/scout_report.log"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "hr-HR,hr;q=0.9,en-US;q=0.8,en;q=0.7",
}

KLJUCNE_VINIL = ['vinil', 'vinyl', 'lp', 'ploč', 'ploc', 'gramofon', 'record', 'singl', '7"', '12"', 'ex-yu', 'exyu']
ZABRANJENE_MERCH = ['majica', 't-shirt', 'hoodie', 'dukser', 'kapa', 'torba', 'pederuša', 'čarape', 'salica', 'šalica', 'stalci', 'oprema', 'ulaznic', 'knjig', 'strip', 'plakat', 'poster', 'privjesak', 'bedž']

class LegacyTlsAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        try:
            ctx.set_ciphers('DEFAULT@SECLEVEL=1')
        except Exception:
            pass
        kwargs['ssl_context'] = ctx
        return super(LegacyTlsAdapter, self).init_poolmanager(*args, **kwargs)

def log_print(poruka=""):
    print(poruka, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(str(poruka) + "\n")

class MultiKlijent:
    def __init__(self, base_url):
        self.base_url = base_url
        self.aktivni_motor = "Nepoznato"
        
        self.cffi = cffi_requests.Session(impersonate="chrome120", verify=False)
        self.cffi.headers.update(HEADERS)
        self.cffi.headers["Referer"] = base_url

        self.cloud = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "desktop": True})
        self.cloud.mount("https://", LegacyTlsAdapter())
        self.cloud.headers.update(HEADERS)
        self.cloud.headers["Referer"] = base_url

    def get(self, url, timeout=20):
        # 1. Prvo pokušavamo curl_cffi (Chrome TLS fingerprint)
        try:
            r = self.cffi.get(url, timeout=timeout)
            if r.status_code in [200, 404] and len(r.content) > 50:
                self.aktivni_motor = "curl_cffi (Chrome 120 TLS Impersonation)"
                return r
        except Exception:
            pass

        # 2. Fallback na Cloudscraper sa SECLEVEL=1 za stare/stroge SSL servere
        try:
            r = self.cloud.get(url, timeout=timeout, verify=False)
            if r.status_code in [200, 404]:
                self.aktivni_motor = "Cloudscraper + LegacyTLS (SECLEVEL=1)"
                return r
        except Exception:
            pass
        return None

def je_li_potencijalna_ploca(tekst_ili_url):
    t = tekst_ili_url.lower()
    if any(z in t for z in ZABRANJENE_MERCH):
        return False
    return any(k in t for k in KLJUCNE_VINIL)

def skeniraj_sitemape(klijent, base_url):
    log_print("\n🔎 [2/6] SKENIRANJE ROBOTS.TXT I SITEMAP.XML STRUKTURE...")
    kandidati_sitemap = []
    
    # 1. Provjeri robots.txt
    r_rob = klijent.get(urljoin(base_url, "/robots.txt"), timeout=10)
    if r_rob and r_rob.status_code == 200 and "user-agent" in r_rob.text.lower():
        for linija in r_rob.text.splitlines():
            if linija.lower().startswith("sitemap:"):
                sm_url = linija.split(":", 1)[1].strip()
                kandidati_sitemap.append(sm_url)
                log_print(f"  🤖 Pronađen Sitemap u robots.txt: {sm_url}")

    # Standardne lokacije sitemapa
    for putanja in ["/sitemap_index.xml", "/sitemap.xml", "/wp-sitemap.xml", "/product-sitemap.xml"]:
        puni = urljoin(base_url, putanja)
        if puni not in kandidati_sitemap:
            kandidati_sitemap.append(puni)

    pronadjeni_pod_sitemapi = []
    pronadjene_kat_iz_sitemapa = []
    pronadjeni_proizvodi_iz_sitemapa = []

    for sm in kandidati_sitemap:
        r_sm = klijent.get(sm, timeout=12)
        if not r_sm or r_sm.status_code != 200 or ("<urlset" not in r_sm.text and "<sitemapindex" not in r_sm.text):
            continue
            
        log_print(f"  ✅ Otvoren glavni Sitemap: {sm}")
        lokacije = re.findall(r'<loc>\s*(https?://[^<\s]+)\s*</loc>', r_sm.text, re.I)
        
        for loc in lokacije:
            loc_l = loc.lower()
            if loc_l.endswith(".xml") or "sitemap" in loc_l:
                if any(k in loc_l for k in ["product", "proizvod", "cat", "kategorij", "post"]):
                    pronadjeni_pod_sitemapi.append(loc)
            else:
                if any(k in loc_l for k in ["category", "kategorija", "gramofonske", "vinyl", "ploce"]):
                    if je_li_potencijalna_ploca(loc_l) and loc not in pronadjene_kat_iz_sitemapa:
                        pronadjene_kat_iz_sitemapa.append(loc)
                elif any(k in loc_l for k in ["/proizvod/", "/product/", "/shop/", "/artikl/", "-lp"]):
                    if je_li_potencijalna_ploca(loc_l) and loc not in pronadjeni_proizvodi_iz_sitemapa:
                        pronadjeni_proizvodi_iz_sitemapa.append(loc)
        break

    # Pretraži pod-sitemape (npr. product_cat-sitemap.xml i product-sitemap1.xml)
    for pod_sm in pronadjeni_pod_sitemapi[:4]:
        log_print(f"  📂 Ulazim u pod-sitemap: {pod_sm}")
        r_pod = klijent.get(pod_sm, timeout=15)
        if not r_pod or r_pod.status_code != 200:
            continue
        lokacije = re.findall(r'<loc>\s*(https?://[^<\s]+)\s*</loc>', r_pod.text, re.I)
        log_print(f"     -> Pronađeno {len(lokacije)} URL-ova u ovom sitemapu.")
        
        for loc in lokacije:
            loc_l = loc.lower()
            if any(k in loc_l for k in ["category", "kategorija", "kolekcija", "collection"]):
                if je_li_potencijalna_ploca(loc_l) and loc not in pronadjene_kat_iz_sitemapa:
                    pronadjene_kat_iz_sitemapa.append(loc)
            else:
                if je_li_potencijalna_ploca(loc_l) and len(pronadjeni_proizvodi_iz_sitemapa) < 15:
                    pronadjeni_proizvodi_iz_sitemapa.append(loc)
                elif len(pronadjeni_proizvodi_iz_sitemapa) < 5 and not any(z in loc_l for z in ZABRANJENE_MERCH):
                    pronadjeni_proizvodi_iz_sitemapa.append(loc)

    if pronadjene_kat_iz_sitemapa:
        log_print(f"  🎯 KATEGORIJE PLOČA IZ SITEMAPA: {pronadjene_kat_iz_sitemapa[:5]}")
    if pronadjeni_proizvodi_iz_sitemapa:
        log_print(f"  🎯 PRIMJERI PROIZVODA IZ SITEMAPA ({len(pronadjeni_proizvodi_iz_sitemapa)}): {pronadjeni_proizvodi_iz_sitemapa[0]}")
    if not pronadjene_kat_iz_sitemapa and not pronadjeni_proizvodi_iz_sitemapa:
        log_print("  ℹ️ Sitemap nije pronađen ili ne sadrži očite URL-ove ploča. Prelazim na API i HTML.")

    return pronadjene_kat_iz_sitemapa, pronadjeni_proizvodi_iz_sitemapa

def provjeri_apije(klijent, base_url):
    log_print("\n🔎 [3/6] DUBINSKA INSPEKCIJA API SUSTAVA (WooCommerce / WP / Shopify)...")
    api_proizvod_link = None
    api_status = "Nema otvorenog API-ja"

    api_rute = [
        ("WooCommerce Store API (v1)", "/wp-json/wc/store/v1/products?per_page=20"),
        ("WooCommerce Store API (Root)", "/wp-json/wc/store/products?per_page=20"),
        ("WordPress REST Product API", "/wp-json/wp/v2/product?per_page=20"),
        ("Shopify JSON API", "/products.json?limit=20")
    ]

    for naziv, ruta in api_rute:
        puni_api = urljoin(base_url, ruta)
        r = klijent.get(puni_api, timeout=15)
        if not r or r.status_code != 200:
            continue
        try:
            data = r.json()
            lista = data.get("products", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            if not lista:
                continue

            log_print(f"  ✅ OTVOREN {naziv}: {ruta} (Vraća artikala u testu: {len(lista)})")
            api_status = naziv
            
            # Analiziramo prvi artikl (po mogućnosti ploču)
            odabrani = lista[0]
            for item in lista:
                ime = str(item.get("name", item.get("title", "")))
                link = str(item.get("permalink", item.get("link", "")))
                if je_li_potencijalna_ploca(ime + " " + link):
                    odabrani = item
                    break

            ime_artikla = odabrani.get("name", odabrani.get("title", "Nepoznato"))
            link_artikla = odabrani.get("permalink", odabrani.get("link", ""))
            if link_artikla:
                api_proizvod_link = link_artikla

            log_print(f"     📦 Uzorak artikla iz API-ja: '{ime_artikla}'")
            if "prices" in odabrani:
                p_info = odabrani["prices"]
                log_print(f"     💶 Cijena u API-ju: {p_info.get('price')} (minor_unit: {p_info.get('currency_minor_unit')})")
            if "attributes" in odabrani and odabrani["attributes"]:
                attr_ispis = []
                for a in odabrani["attributes"]:
                    a_ime = a.get("name", "")
                    a_terms = [t.get("name", "") for t in a.get("terms", [])]
                    attr_ispis.append(f"{a_ime}: {a_terms}")
                log_print(f"     🏷️ Atributi u API-ju: {' | '.join(attr_ispis)}")
                if any("rabljeno" in str(a).lower() for a in attr_ispis) and not any(re.search(r'\b(VG|NM|EX|MINT)\b', str(a), re.I) for a in attr_ispis):
                    log_print("     ⚠️ OPREZ: API u atributima vraća samo generičko 'Rabljeno'! Za prave VG+/NM ocjene scraper MORA čitati HTML opis ili ulaziti u proizvod!")
            else:
                log_print("     ℹ️ API ne sadrži taksonomske atribute stanja (provjerit ću HTML stranicu proizvoda).")
            break
        except Exception:
            continue

    if api_status == "Nema otvorenog API-ja":
        log_print("  ❌ Nijedan standardni JSON API nije otvoren. Scraper mora raditi čisti HTML parsing.")
    return api_status, api_proizvod_link

def analiziraj_html_i_navigaciju(klijent, ulazni_url, base_url, kat_iz_sitemapa):
    log_print("\n🔎 [4/6] ANALIZA HTML STRUKTURE, NAVIGACIJE, TABLICA I PAGINACIJE...")
    r = klijent.get(ulazni_url, timeout=20)
    if not r:
        log_print("  ❌ Kritična greška: Ne mogu dohvatiti naslovnicu!")
        return None, None

    soup = BeautifulSoup(r.content, "lxml")
    
    # 1. Traženje linkova u navigaciji (uključujući i Roxy abecedne A.htm, B.htm linkove!)
    kategorije_u_meniju = []
    abecedni_linkovi = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        puni = urljoin(base_url, href)
        if not puni.startswith(base_url) or "#" in href or "javascript:" in href.lower():
            continue
        tekst = a.get_text(" ", strip=True)
        
        # Detekcija Roxy / Retro abecednih kataloga (A.htm, B.htm ili tekst linka je samo jedno slovo)
        if re.search(r'/[a-zA-Z0-9]{1,3}\.html?$', puni) or (len(tekst) == 1 and tekst.isalpha() and href.endswith(('.htm', '.html', '.php'))):
            if not any(ig in puni.lower() for ig in ['stalci', 'oprema', 'kontakt', 'onama', 'narudzba']):
                abecedni_linkovi.append(puni)
        elif je_li_potencijalna_ploca(tekst + " " + href):
            if not any(k in puni.lower() for k in ['?add-to-cart', '/cart', '/kosarica', '/moj-racun', 'otkup']):
                kategorije_u_meniju.append((tekst, puni))

    abecedni_linkovi = list(dict.fromkeys(abecedni_linkovi))
    if abecedni_linkovi:
        log_print(f"  🦕 DETEKTIRAN ABECEDNI / RETRO KATALOG ({len(abecedni_linkovi)} podstranica): npr. {abecedni_linkovi[:4]}")

    # Spajamo kategorije iz sitemapa i menija za provjeru kataloga
    sve_kategorije_za_provjeru = list(dict.fromkeys(kat_iz_sitemapa + [u for _, u in kategorije_u_meniju] + abecedni_linkovi))
    if sve_kategorije_za_provjeru:
        log_print(f"  🧭 Pronađene ciljane podstranice/kategorije ({len(sve_kategorije_za_provjeru)}): {sve_kategorije_za_provjeru[:4]}")

    # Odabiremo najbolju stranicu kataloga za dubinsku provjeru kartica/tablica i paginacije
    stranice_za_test = [ulazni_url] + sve_kategorije_za_provjeru[:5]
    prvi_proizvod_link = None
    detektirana_paginacija = None

    for test_url in stranice_za_test:
        r_test = r if test_url == ulazni_url else klijent.get(test_url, timeout=20)
        if not r_test or r_test.status_code != 200:
            continue
        s_test = BeautifulSoup(r_test.content, "lxml")

        # A) PROVJERA ZA RETRO TABLICE (Roxy stil)
        svi_tr = s_test.find_all("tr")
        if len(svi_tr) > 15:
            log_print(f"  📊 PRONAĐENA TABLIČNA BAZA na: {test_url} (Ukupno redova: {len(svi_tr)})")
            uzorci_redova = []
            for tr in svi_tr[:35]:
                cols = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
                if len(cols) >= 4:
                    uzorci_redova.append(cols)
            if uzorci_redova:
                log_print(f"     -> Broj stupaca u tablici: {len(uzorci_redova[0])}")
                for idx, ur in enumerate(uzorci_redova[:4]):
                    log_print(f"     -> Uzorak reda #{idx+1}: {ur}")
            return "TABLICA", test_url

        # B) PROVJERA ZA MODERNE KARTICE PROIZVODA
        selektori_kartica = [
            "li.product", "div.product", ".type-product", ".product-item", 
            ".product-layout", ".list-item", ".item", ".product-grid-item", "article.product"
        ]
        for sel in selektori_kartica:
            kartice = s_test.select(sel)
            if len(kartice) >= 3:
                log_print(f"  📦 Pronađena mreža proizvoda na {test_url} -> CSS Selektor: '{sel}' ({len(kartice)} kom na stranici)")
                
                # Provjera paginacije na toj stranici
                for a_pag in s_test.find_all("a", href=True):
                    h_pag = a_pag["href"]
                    if re.search(r'(/page/\d+|[?&]page=\d+|[?&]paged=\d+|[?&]p=\d+)', h_pag):
                        detektirana_paginacija = urljoin(base_url, h_pag)
                        break
                if detektirana_paginacija:
                    log_print(f"  📄 Detektiran format PAGINACIJE: {detektirana_paginacija}")

                # Provjera krije li se stanje već na samoj kartici (kao na Woodstocku)
                uzorak_tekst = kartice[0].get_text(" ", strip=True)
                if re.search(r'(Stanje:|Stanje omota:|Very Good|Near Mint|\(VG|\(NM)', uzorak_tekst, re.I):
                    log_print(f"  💡 VAŽNO: Ocjene stanja pišu DIREKTNO NA KARTICI KATEGORIJE (ne treba otvarati svaki proizvod posebno)!")
                    log_print(f"     -> Tekst prve kartice: '{uzorak_tekst[:180]}...'")

                # Izvuci link na pravu ploču
                for k in kartice:
                    tekst_k = k.get_text(" ", strip=True)
                    for a_k in k.find_all("a", href=True):
                        href_k = urljoin(base_url, a_k["href"])
                        if "?add-to-cart" in href_k or href_k.rstrip("/") == test_url.rstrip("/"):
                            continue
                        if je_li_potencijalna_ploca(tekst_k + " " + href_k):
                            prvi_proizvod_link = href_k
                            break
                    if prvi_proizvod_link:
                        break
                
                if not prvi_proizvod_link:
                    # Fallback na prvi link u prvoj kartici ako nisu pisale riječi LP/Vinyl u naslovu
                    for a_k in kartice[0].find_all("a", href=True):
                        href_k = urljoin(base_url, a_k["href"])
                        if "?add-to-cart" not in href_k:
                            prvi_proizvod_link = href_k
                            break
                break
        if prvi_proizvod_link:
            break

    return "KARTICE", prvi_proizvod_link

def dubinska_forenzika_proizvoda(klijent, proizvod_url):
    log_print(f"\n🔎 [5/6] FORENZIKA POJEDINAČNOG PROIZVODA: {proizvod_url}")
    r = klijent.get(proizvod_url, timeout=20)
    if not r or r.status_code != 200:
        log_print("  ❌ Ne mogu otvoriti stranicu proizvoda.")
        return

    soup = BeautifulSoup(r.content, "lxml")

    # 1. Slika
    og_img = soup.find("meta", property="og:image")
    if og_img and og_img.get("content"):
        log_print(f"  🖼️ Slika (og:image): Dostupna -> {og_img['content']}")
    else:
        img_el = soup.select_one(".woocommerce-product-gallery__image img, .product-images img, img.wp-post-image")
        log_print(f"  🖼️ Slika (HTML galerija): {img_el.get('src') if img_el else 'Nije pronađena'}")

    # 2. Cijena
    cijena_el = soup.select_one("p.price, span.price, .product-price, .summary .price, .panel-footer strong")
    if cijena_el:
        log_print(f"  💶 Cijena na stranici: '{cijena_el.get_text(' ', strip=True)}' (Tag: <{cijena_el.name} class='{cijena_el.get('class')}'>)")

    # 3. Detekcija stanja (Medij / Omot) u svim opisima i tablicama atributa
    uzorci_stanja = re.compile(r'(stanje|condition|sleeve|omot|ploča|vinil|media|grading|ocjena|rabljeno|novo|sealed)', re.I)
    ocjene_regex = re.compile(r'\b(M|NM|M-|EX\+|EX|EX-|VG\+|VG|VG-|G\+|G|F|P|Novo|New|Sealed|Rabljeno)\b', re.I)
    
    pronadjeno = []
    for el in soup.find_all(["tr", "li", "p", "div", "span"]):
        # Izbjegavamo divove koji drže cijelu stranicu
        if len(el.find_all(["div", "p", "tr"])) > 3:
            continue
        tekst = el.get_text(" ", strip=True)
        if 3 < len(tekst) < 280 and uzorci_stanja.search(tekst) and ocjene_regex.search(tekst):
            klasa = " ".join(el.get("class", [])) if el.get("class") else "bez-klase"
            p_klasa = " ".join(el.parent.get("class", [])) if (el.parent and el.parent.get("class")) else "bez-klase"
            zapis = f"<{el.name} class='{klasa}' (parent: '{p_klasa}')> => \"{tekst}\""
            if zapis not in pronadjeno:
                pronadjeno.append(zapis)

    if pronadjeno:
        log_print("  🎯 PRONAĐENE LOKACIJE STANJA PLOČE / OMOTA U HTML-u:")
        for p in pronadjeno[:10]:
            log_print(f"     ✅ {p}")
    else:
        log_print("  ℹ️ U HTML-u proizvoda nema oznaka stanja (VG+/NM). Trgovina vjerojatno prodaje isključivo NOVE ploče.")

def izvidac(url):
    if not url:
        print("❌ GREŠKA: Nije zadan TARGET_URL!")
        sys.exit(1)
    if not url.startswith("http"):
        url = "https://" + url

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write(f"=== SCOUT 2.0 FORENZIČKI IZVJEŠTAJ ({time.strftime('%d.%m.%Y %H:%M:%S')}) ===\n")

    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    log_print(f"🚀 POKREĆEM KOMPLETNU FORENZIKU ZA DOMENU: {base_url}")
    log_print(f"🔗 Ulazni URL: {url}")
    log_print("=" * 70)

    klijent = MultiKlijent(base_url)

    # FAZA 1: Zaštita i SSL
    log_print("🔎 [1/6] DIJAGNOSTIKA ZAŠTITE, SERVERA I SSL/TLS PROTOKOLA...")
    r_home = klijent.get(url, timeout=20)
    if not r_home:
        log_print("  ❌ Kritično: Server odbija sve konekcije ili domena ne postoji!")
        return
    
    server_hdr = r_home.headers.get("Server", "Nepoznato")
    cf_ray = "DA (Cloudflare aktivan)" if "cf-ray" in {k.lower() for k in r_home.headers.keys()} else "NE"
    log_print(f"  ✅ HTTP Status: {r_home.status_code}")
    log_print(f"  🛡️ Uspješni mrežni motor: {klijent.aktivni_motor}")
    log_print(f"  🖥️ Server: {server_hdr} | Cloudflare: {cf_ray}")

    # FAZA 2: Sitemap & Robots.txt
    kat_sitemap, prod_sitemap = skeniraj_sitemape(klijent, base_url)

    # FAZA 3: API Inspekcija
    api_status, api_prod_link = provjeri_apije(klijent, base_url)

    # FAZA 4: HTML, Navigacija, Tablice i Paginacija
    tip_strukture, html_prod_link = analiziraj_html_i_navigaciju(klijent, url, base_url, kat_sitemap)

    # FAZA 5: Dubinska analiza konkretnog proizvoda
    if tip_strukture != "TABLICA":
        ciljani_proizvod = prod_sitemap[0] if prod_sitemap else (html_prod_link or api_prod_link)
        if ciljani_proizvod:
            dubinska_forenzika_proizvoda(klijent, ciljani_proizvod)
        else:
            log_print("\n🔎 [5/6] FORENZIKA PROIZVODA: Nije pronađen nijedan pojedinačni link proizvoda.")

    # FAZA 6: Zaključak za pisanje scrapera
    log_print("\n🔎 [6/6] ZAKLJUČAK I PREPORUKA ZA PISANJE SCRAPERA:")
    if tip_strukture == "TABLICA":
        log_print("  👉 ARHITEKTURA: Statični tablični / abecedni katalog (poput Roxyja). Koristi HTML parser po svim .htm/.html podstranicama i čitaj <tr>/<td> stupce.")
    elif api_status != "Nema otvorenog API-ja":
        log_print(f"  👉 ARHITEKTURA: Koristi brzi {api_status}, ali provjeri u točki [3/6] i [5/6] jesu li VG+/NM ocjene u API-ju ili trebaš kombinirati API + HTML Deep Inspection.")
    else:
        log_print("  👉 ARHITEKTURA: Klasični HTML scraper po kategorijama uz paginaciju detektiranu u točki [4/6].")

    log_print("=" * 70)
    log_print("🏁 Forenzika završena! Izvještaj spremljen u logs/scout_report.log")

if __name__ == "__main__":
    izvidac(CILJANI_URL)
