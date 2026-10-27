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
BROJ_NITI = 4

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
            if r.status_code == 200 and len(r.text) > 2000:
                return r.text
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
    # Woodstock u naslovu često stavlja format unutar zagrada npr. (LP, Album) ili (12")
    if any(oznaka in n_up for oznaka in [' 7"', " 7''", " 7″", " 7-INCH", " EP", " SINGLE"]):
        return 'Singl 7"'
    if any(oznaka in n_up for oznaka in [' 12"', ' 10"', " LP", " 2XLP"]):
        return "LP"
    return "LP" # Default ako ne piše

def izvuci_najbolju_sliku(img_el):
    if not img_el:
        return ""
    src = img_el.get("src", "").strip()
    if src:
        # Woodstock ponekad stavlja relativne URL-ove
        return urljoin(SITE_URL, src)
    return ""

def izvuci_cijenu(el):
    # Cijena se nalazi u <strong> unutar .panel-footer
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
    # Stanje ploče i omota su obično u .panel-heading ispod .list-item-title
    stanje_medija = "Rabljeni"
    stanje_omota = "Rabljeni"
    
    opis_div = el.select_one(".panel-heading .hidden-xs")
    if not opis_div:
        opis_div = el.select_one(".panel-heading")

    if opis_div:
        opis_tekst = opis_div.get_text(" ", strip=True)
        # Primjer: "Stanje: Very Good Plus (VG+) Stanje omota: Near Mint (NM or M-)"
        
        m_medij = re.search(r"Stanje:\s*([^(]+(?:\([^)]+\))?)", opis_tekst, re.IGNORECASE)
        if m_medij:
            stanje_medija = m_medij.group(1).split("Stanje omota:")[0].strip()
            # Izvuci samo kratku oznaku ako postoji, npr. (VG+) -> VG+
            kratko = re.search(r"\(([^)]+)\)", stanje_medija)
            if kratko:
                stanje_medija = kratko.group(1)

        m_omot = re.search(r"Stanje omota:\s*([^(]+(?:\([^)]+\))?)", opis_tekst, re.IGNORECASE)
        if m_omot:
            stanje_omota = m_omot.group(1).strip()
            kratko = re.search(r"\(([^)]+)\)", stanje_omota)
            if kratko:
                stanje_omota = kratko.group(1).split(" or ")[0] # npr. NM or M- -> NM

    # Ponekad artikl piše "NOVO" u naslovu
    naslov = ocisti_naziv(el.select_one(".list-item-title a").get_text(" ", strip=True)) if el.select_one(".list-item-title a") else ""
    if "NOVO" in naslov.upper() or "SEALED" in naslov.upper():
        stanje_medija = "Novo"
        stanje_omota = "Novo"

    return stanje_medija, stanje_omota

def izvuci_broj_stranica(soup):
    max_str = 1
    for a in soup.select("ul.pagination a"):
        tekst = a.get_text(strip=True)
        if tekst.isdigit():
            max_str = max(max_str, int(tekst))
        
        href = a.get("href", "")
        m = re.search(r"page=(\d+)", href)
        if m:
            max_str = max(max_str, int(m.group(1)))
    return max_str

def normaliziraj_link(href):
    if not href:
        return ""
    puni = urljoin(SITE_URL, html.unescape(str(href)).strip())
    # Uklanjamo query parametre sa linka na artikl da izbjegnemo duplikate
    return puni.split("?")[0]

def parsiraj_stranicu(html_tekst):
    soup = BeautifulSoup(html_tekst, "lxml")
    stavke = []
    kartice = soup.select(".list-item")
    
    vidjeni_na_stranici = set()

    for k in kartice:
        # Naslov i link
        title_a = k.select_one(".list-item-title a")
        if not title_a:
            continue
            
        link = normaliziraj_link(title_a.get("href", ""))
        if not link or link in vidjeni_na_stranici:
            continue
            
        naslov = ocisti_naziv(title_a.get_text(" ", strip=True))
        if not naslov:
            continue

        cijena = izvuci_cijenu(k)
        if cijena == "0.00":
            continue

        slika_url = izvuci_najbolju_sliku(k.select_one("img"))
        stanje_medija, stanje_omota = izvuci_stanje(k)
        tip_artikla = odredi_tip_artikla(naslov)

        vidjeni_na_stranici.add(link)
        red = [naslov, cijena, link, slika_url, stanje_medija, stanje_omota, tip_artikla]
        stavke.append((link, red))

    max_str = izvuci_broj_stranica(soup)
    return stavke, len(kartice), max_str

def obradi_stranicu_zadatka(args):
    url, page = args
    # Dodajemo page={page} i limit=100 parametar u nadi da webshop to podržava
    page_url = f"{url}?page={page}&limit=100" 
    
    t_start = time.time()
    html_tekst = sigurni_get(page_url)
    
    if not html_tekst:
        return page, False, [], 0, 1, time.time() - t_start
        
    stavke, br_kartica, max_str = parsiraj_stranicu(html_tekst)
    return page, True, stavke, br_kartica, max_str, time.time() - t_start

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
        print(f"📦 Učitano {len(sve_ploce)} postojećih zapisa iz {CSV_FILENAME}.", flush=True)

    ukupno_neuspjelih = 0

    with ThreadPoolExecutor(max_workers=BROJ_NITI) as executor:
        for naziv_kat, kat_url in KATEGORIJE:
            print(f"\n📂 Analiziram kategoriju: '{naziv_kat}' -> {kat_url}", flush=True)

            _, ok1, stavke1, br_kartica1, max_pages, _ = obradi_stranicu_zadatka((kat_url, 1))
            if not ok1 or br_kartica1 == 0:
                print(f"   ℹ️ Nema pronađenih artikala u '{naziv_kat}'.", flush=True)
                continue

            novih_u_kat = 0
            for link, red in stavke1:
                if link not in aktivni_linkovi:
                    novih_u_kat += 1
                sve_ploce[link] = red
                aktivni_linkovi.add(link)

            print(f"   ✅ Stranica 1/{max_pages}: +{len(stavke1)} artikala | Ukupno: {len(aktivni_linkovi)}", flush=True)

            if max_pages > 1:
                preostale_stranice = list(range(2, max_pages + 1))
                
                # Blok po blok zbog stabilnosti
                for i in range(0, len(preostale_stranice), BROJ_NITI):
                    blok = preostale_stranice[i:i + BROJ_NITI]
                    zadaci = [(kat_url, p) for p in blok]
                    t_blok = time.time()

                    rezultati = list(executor.map(obradi_stranicu_zadatka, zadaci))
                    uspjesno_str = 0
                    novih_u_bloku = 0

                    for page, ok, stavke, br_kartica, _, _ in rezultati:
                        if not ok:
                            ukupno_neuspjelih += 1
                            continue
                        uspjesno_str += 1
                        for link, red in stavke:
                            if link not in aktivni_linkovi:
                                novih_u_bloku += 1
                                novih_u_kat += 1
                            sve_ploce[link] = red
                            aktivni_linkovi.add(link)

                    print(
                        f"   ⚡ Stranice {blok[0]:02d}-{blok[-1]:02d}/{max_pages} "
                        f"({uspjesno_str}/{len(blok)} OK) za {time.time() - t_blok:.1f}s | "
                        f"+{novih_u_bloku} novih | Ukupno: {len(aktivni_linkovi)}",
                        flush=True
                    )

            print(f"   🏁 Kategorija '{naziv_kat}' završena (+{novih_u_kat} unikatnih ploča).", flush=True)

    if ukupno_neuspjelih == 0 and len(aktivni_linkovi) > 50:
        pocetni_broj = len(sve_ploce)
        sve_ploce = {k: v for k, v in sve_ploce.items() if k in aktivni_linkovi}
        obrisano = pocetni_broj - len(sve_ploce)
        print(f"\n🧹 Sinkronizacija završena! Uklonjeno {obrisano} prodanih artikala iz stare baze.", flush=True)
    else:
        print(f"\n⚠️ Oprez: Zabilježeno {ukupno_neuspjelih} neuspjelih stranica. Preskačem brisanje.", flush=True)

    if sve_ploce:
        with open(CSV_FILENAME, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Naslov", "Cijena", "URL_Proizvoda", "URL_Slike", "Stanje_Medija", "Stanje_Omota", "Tip_Artikla"])
            writer.writerows(sve_ploce.values())
        ukupno_slika = sum(1 for v in sve_ploce.values() if len(v) >= 4 and v[3])
        print(f"🎉 GOTOVO za {time.time() - t_start:.1f}s! U bazi je {len(sve_ploce)} dostupnih Woodstock vinila (Sa slikom: {ukupno_slika}).", flush=True)

if __name__ == "__main__":
    scrape_woodstock()
