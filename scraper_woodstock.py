import cloudscraper
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin
import urllib3
import threading
import csv
import os
import re
import time
import html

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SITE_URL = "https://www.woodstock-trgovina.hr"
CSV_FILENAME = "woodstock_ploce.csv"
BROJ_NITI = 8 

KATEGORIJE = [
    ("Gramofonske ploče", "https://www.woodstock-trgovina.hr/hr/gramofonske-ploce/p-2"),
    ("Ex-YU izdanja", "https://www.woodstock-trgovina.hr/hr/ex-yu-izdanja/p-7")
]

_thread_local = threading.local()

def kreiraj_sesiju():
    s = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "hr-HR,hr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": SITE_URL,
        "Connection": "keep-alive"
    })
    return s

def dohvati_sesiju():
    s = getattr(_thread_local, "session", None)
    if s is None:
        s = kreiraj_sesiju()
        _thread_local.session = s
    return s

def sigurni_get(url, timeout=30, max_pokusaja=4):
    for pokusaj in range(1, max_pokusaja + 1):
        try:
            s = dohvati_sesiju()
            r = s.get(url, timeout=timeout)
            if r.status_code == 200 and len(r.text) > 500:
                return r.text
            if r.status_code == 404:
                return "STATUS_404"
            time.sleep(1.5 * pokusaj)
            _thread_local.session = kreiraj_sesiju()
        except Exception as e:
            print(f"   ⚠️ Greška spajanja ({pokusaj}/{max_pokusaja}) na {url}: {e}", flush=True)
            time.sleep(1.5 * pokusaj)
            _thread_local.session = kreiraj_sesiju()
    return None

def ocisti_naziv(sirovi_naslov):
    cisti = html.unescape(sirovi_naslov)
    cisti = cisti.replace("–", "-").replace("—", "-")
    return " ".join(cisti.split()).strip()

def odredi_tip_artikla(naziv):
    n_up = naziv.upper()
    if any(oznaka in n_up for oznaka in [' 7"', " 7''", " 7″", " 7-INCH", " EP", " SINGLE"]):
        return 'Singl 7"'
    if any(oznaka in n_up for oznaka in [' 12"', ' 10"', " LP", " 2XLP"]):
        return "LP"
    return "LP"

def izvuci_najbolju_sliku(img_el):
    if not img_el:
        return ""
    src = img_el.get("src", "").strip()
    if src:
        return urljoin(SITE_URL, src)
    return ""

def izvuci_cijenu(el):
    price_el = el.select_one(".panel-footer strong")
    if price_el:
        tekst = price_el.get_text(" ", strip=True).upper().replace("EUR", "").strip()
        tekst = tekst.replace(".", "").replace(",", ".") if "," in tekst and "." in tekst else tekst.replace(",", ".")
        m = re.search(r"(\d+(?:\.\d{1,2})?)", tekst)
        if m:
            try:
                val = float(m.group(1))
                if val > 0:
                    return f"{val:.2f}"
            except Exception:
                pass
    return "0.00"

def izvuci_stanje(el):
    medij = "VG+"
    omot = "VG+"
    
    tekst = el.get_text(" ", strip=True)
    
    # 1. Regex analiza čistog teksta
    m_medij = re.search(r'Stanje:\s*(.*?)(?=\s*(?:Stanje omota:|Format:|Godina:|Žanr:|Stil:|Kataloški|$))', tekst, re.IGNORECASE)
    m_omot = re.search(r'Stanje omota:\s*(.*?)(?=\s*(?:Format:|Godina:|Žanr:|Stil:|Kataloški|$))', tekst, re.IGNORECASE)
    
    # Funkcija koja pretvara prljavi tekst (npr. "Near Mint (NM or M-) Kupi za 25 EUR") u standardnu oznaku
    def ocisti(sirovo):
        s = sirovo.upper()
        if "NOVO" in s or "SEALED" in s: return "Novo"
        if "NEAR MINT" in s or "(NM" in s: return "NM"
        if "VERY GOOD PLUS" in s or "(VG+)" in s: return "VG+"
        if "VERY GOOD" in s or "(VG)" in s: return "VG"
        if "MINT" in s or "(M)" in s: return "M"
        if "EXCELLENT" in s or "(EX)" in s: return "EX"
        if "GOOD PLUS" in s or "(G+)" in s: return "G+"
        if " GOOD " in s or "(G)" in s: return "G"
        return "VG+" # Fallback

    if m_medij:
        v = m_medij.group(1).strip()
        if v: medij = ocisti(v)
        
    if m_omot:
        v = m_omot.group(1).strip()
        if v: omot = ocisti(v)
    
    # 2. Dodatna provjera za nove ploče iz naslova
    title_a = el.select_one(".list-item-title a")
    naslov = ocisti_naziv(title_a.get_text(" ", strip=True)) if title_a else ""
    if "NOVO" in naslov.upper() or "SEALED" in naslov.upper():
        medij = "Novo"
        omot = "Novo"
        
    return medij, omot

def normaliziraj_link(href):
    if not href:
        return ""
    puni = urljoin(SITE_URL, html.unescape(str(href)).strip())
    return puni.split("?")[0]

def parsiraj_stranicu(html_tekst):
    soup = BeautifulSoup(html_tekst, "lxml")
    stavke = []
    kartice = soup.select(".list-item")
    
    for k in kartice:
        title_a = k.select_one(".list-item-title a")
        if not title_a:
            continue
            
        link = normaliziraj_link(title_a.get("href", ""))
        naslov = ocisti_naziv(title_a.get_text(" ", strip=True))
        if not link or not naslov:
            continue

        cijena = izvuci_cijenu(k)
        if cijena == "0.00":
            continue

        slika_url = izvuci_najbolju_sliku(k.select_one("img"))
        stanje_medija, stanje_omota = izvuci_stanje(k)
        tip_artikla = odredi_tip_artikla(naslov)

        red = [naslov, cijena, link, slika_url, stanje_medija, stanje_omota, tip_artikla]
        stavke.append((link, red))

    return stavke, len(kartice)

def obradi_stranicu_zadatka(args):
    url, page = args
    page_url = f"{url}?page={page}" 
    
    t_start = time.time()
    html_tekst = sigurni_get(page_url)
    
    if not html_tekst or html_tekst == "STATUS_404":
        return page, False, [], 0, time.time() - t_start
        
    stavke, br_kartica = parsiraj_stranicu(html_tekst)
    return page, True, stavke, br_kartica, time.time() - t_start

def scrape_woodstock():
    t_start = time.time()
    sve_ploce = {}
    aktivni_linkovi = set()

    if os.path.exists(CSV_FILENAME):
        with open(CSV_FILENAME, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) >= 7:
                    norm_k = normaliziraj_link(row[2])
                    row[2] = norm_k
                    sve_ploce[norm_k] = row

    ukupno_neuspjelih = 0

    with ThreadPoolExecutor(max_workers=BROJ_NITI) as executor:
        for naziv_kat, kat_url in KATEGORIJE:
            print(f"\n📂 Analiziram kategoriju: '{naziv_kat}' -> {kat_url}", flush=True)
            
            kraj_kategorije = False
            trenutna_stranica = 1
            ukupno_u_kat = 0

            while not kraj_kategorije:
                blok_stranica = list(range(trenutna_stranica, trenutna_stranica + BROJ_NITI))
                zadaci = [(kat_url, p) for p in blok_stranica]
                t_blok = time.time()
                
                rezultati = list(executor.map(obradi_stranicu_zadatka, zadaci))
                novih_u_bloku = 0
                stranica_sa_nula_novih = 0
                
                for page, ok, stavke, br_kartica, _ in rezultati:
                    if not ok:
                        ukupno_neuspjelih += 1
                        kraj_kategorije = True
                        continue
                        
                    if br_kartica == 0:
                        kraj_kategorije = True
                        
                    novih_na_ovoj_str = 0
                    for link, red in stavke:
                        if link not in aktivni_linkovi:
                            novih_na_ovoj_str += 1
                            novih_u_bloku += 1
                        sve_ploce[link] = red
                        aktivni_linkovi.add(link)
                        
                    if br_kartica > 0 and novih_na_ovoj_str == 0:
                        stranica_sa_nula_novih += 1
                        
                ukupno_u_kat += novih_u_bloku
                
                if stranica_sa_nula_novih == len(blok_stranica) and novih_u_bloku == 0:
                    kraj_kategorije = True

                print(
                    f"   ⚡ Stranice {blok_stranica[0]:02d}-{blok_stranica[-1]:02d} | "
                    f"+{novih_u_bloku} ploča ({time.time() - t_blok:.1f}s) | Ukupno: {len(aktivni_linkovi)}",
                    flush=True
                )
                
                trenutna_stranica += BROJ_NITI

    if ukupno_neuspjelih == 0 and len(aktivni_linkovi) > 50:
        pocetni_broj = len(sve_ploce)
        sve_ploce = {k: v for k, v in sve_ploce.items() if k in aktivni_linkovi}
        print(f"\n🧹 Sinkronizacija završena! Uklonjeno {pocetni_broj - len(sve_ploce)} prodanih artikala.", flush=True)

    if sve_ploce:
        with open(CSV_FILENAME, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Naslov", "Cijena", "URL_Proizvoda", "URL_Slike", "Stanje_Medija", "Stanje_Omota", "Tip_Artikla"])
            writer.writerows(sve_ploce.values())
        print(f"🎉 GOTOVO za {time.time() - t_start:.1f}s! U bazi je {len(sve_ploce)} Woodstock ploča.", flush=True)

if __name__ == "__main__":
    scrape_woodstock()
