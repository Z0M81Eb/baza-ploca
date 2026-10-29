import cloudscraper
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin
import urllib3
import csv
import os
import re
import time
import html

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SITE_URL = "https://www.cdshop-roxy.com"
CSV_FILENAME = "roxy_ploce.csv"
BROJ_NITI = 5

def kreiraj_sesiju():
    s = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "hr-HR,hr;q=0.9,en-US;q=0.8,en;q=0.7",
    })
    return s

def ocisti_cijenu(sirovo):
    cisti = sirovo.replace("€", "").replace("EUR", "").strip()
    if cisti.endswith("."):
        cisti = cisti[:-1]
    cisti = cisti.replace(",", ".")
    try:
        val = float(cisti)
        if val > 0:
            return f"{val:.2f}"
    except:
        pass
    return "0.00"

def odredi_tip(tip_sirovo):
    t_low = tip_sirovo.lower()
    if '7"' in t_low or 'singl' in t_low:
        return 'Singl 7"'
    elif '12"' in t_low or 'maxi' in t_low:
        return 'Maxi 12"'
    return 'LP'

def dohvati_katalog_linkove(sesija):
    print("🔎 Dohvaćam abecedne linkove iz RoxY naslovnice...", flush=True)
    try:
        r = sesija.get(SITE_URL, timeout=20)
        soup = BeautifulSoup(r.content, "lxml")
        linkovi = set()
        
        ignoriraj = ["prodaja", "zamjena", "clanovi", "forme", "narudzba", "knjiga", "stalci"]
        
        for a in soup.select("a[href]"):
            href = a["href"].strip()
            if (href.endswith(".htm") or href.endswith(".html")) and not href.startswith("http"):
                if not any(ig in href.lower() for ig in ignoriraj):
                    linkovi.add(urljoin(SITE_URL, href))
                    
        return list(linkovi)
    except Exception as e:
        print(f"❌ Greška pri dohvaćanju naslovnice: {e}", flush=True)
        return []

def obradi_stranicu(url):
    s = kreiraj_sesiju()
    t_start = time.time()
    stavke = []
    
    try:
        r = s.get(url, timeout=30)
        if r.status_code != 200:
            return False, stavke, 0
            
        soup = BeautifulSoup(r.content, "lxml")
        redovi = soup.select("tr")
        
        for tr in redovi:
            tdovi = tr.find_all(['td', 'th'])
            cols = [td.get_text(" ", strip=True) for td in tdovi]
            if len(cols) < 8:
                continue
                
            medij_tip = cols[0].upper()
            format_tip = cols[1].upper()
            
            # STROGI, NEPROBOJNI ANTI-CD FILTER
            # Ako sadrži ikakav trag CD-a, DVD-a ili Kazete, odmah ubijamo redak!
            if "CD" in medij_tip or "CD" in format_tip or "DVD" in medij_tip or "KAS" in medij_tip or "KAZ" in medij_tip:
                continue
                
            # Tražimo da u Mediju barem piše "PLO" (hvata PLOČA i PLOCA) ili da je Format LP/7"
            if "PLO" not in medij_tip and "LP" not in format_tip and "7\"" not in format_tip and "12\"" not in format_tip:
                continue
                
            izvodjac = cols[2].strip()
            naslov_albuma = cols[3].strip()
            puni_naslov = f"{izvodjac} - {naslov_albuma}"
            puni_naslov = " ".join(puni_naslov.split()).strip(" -")
            kondicija = cols[5].strip()
            cijena_euro = cols[7].strip()
            
            cijena = ocisti_cijenu(cijena_euro)
            if cijena == "0.00":
                continue
                
            slug = re.sub(r'[^a-z0-9]+', '-', puni_naslov.lower()).strip('-')
            jedinstveni_link = f"{url}#{slug}"
            
            stanje_medija = "VG"
            stanje_omota = "VG"
            if "/" in kondicija and kondicija != "/":
                dijelovi = kondicija.split("/")
                stanje_medija = dijelovi[0].strip().lstrip("-") or "VG"
                stanje_omota = dijelovi[1].strip().lstrip("-") or "VG"
            elif kondicija and kondicija != "/":
                stanje_medija = kondicija
                stanje_omota = kondicija
                
            tip_artikla = odredi_tip(format_tip)
            slika_url = "" 
            
            red = [puni_naslov, cijena, jedinstveni_link, slika_url, stanje_medija, stanje_omota, tip_artikla]
            stavke.append((jedinstveni_link, red))
                    
        print(f"   ⚡ Stranica {url.split('/')[-1]} obrađena za {time.time() - t_start:.1f}s | Pronađeno ploča: {len(stavke)}", flush=True)
        return True, stavke, len(stavke)
        
    except Exception as e:
        print(f"   ⚠️ Greška na {url}: {e}", flush=True)
        return False, [], 0

def scrape_roxy():
    t_start = time.time()
    sve_ploce = {}
    aktivni_linkovi = set()

    if os.path.exists(CSV_FILENAME):
        with open(CSV_FILENAME, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) >= 7:
                    sve_ploce[row[2]] = row

    sesija = kreiraj_sesiju()
    stranice = dohvati_katalog_linkove(sesija)
    
    if not stranice:
        print("❌ Nije pronađena niti jedna stranica kataloga. Prekidam.", flush=True)
        return
        
    print(f"📂 Pronađeno {len(stranice)} stranica kataloga. Pokrećem preuzimanje ploča...", flush=True)

    ukupno_neuspjelih = 0

    with ThreadPoolExecutor(max_workers=BROJ_NITI) as executor:
        rezultati = list(executor.map(obradi_stranicu, stranice))
        
        for ok, stavke, br_dodano in rezultati:
            if not ok:
                ukupno_neuspjelih += 1
            for link, red in stavke:
                sve_ploce[link] = red
                aktivni_linkovi.add(link)

    if ukupno_neuspjelih == 0 and len(aktivni_linkovi) > 100:
        pocetni_broj = len(sve_ploce)
        sve_ploce = {k: v for k, v in sve_ploce.items() if k in aktivni_linkovi}
        obrisano = pocetni_broj - len(sve_ploce)
        print(f"\n🧹 Sinkronizacija završena! Uklonjeno {obrisano} rasprodanih artikala iz stare baze.", flush=True)
    else:
        print(f"\n⚠️ Oprez: Zabilježeno {ukupno_neuspjelih} neuspjelih stranica. Preskačem brisanje stare baze radi sigurnosti.", flush=True)

    if sve_ploce:
        with open(CSV_FILENAME, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Naslov", "Cijena", "URL_Proizvoda", "URL_Slike", "Stanje_Medija", "Stanje_Omota", "Tip_Artikla"])
            writer.writerows(sve_ploce.values())
        print(f"🎉 GOTOVO za {time.time() - t_start:.1f}s! U bazi je {len(sve_ploce)} dostupnih ploča iz RoxY-ja (Svi CD-ovi su brutalno odbačeni).", flush=True)

if __name__ == "__main__":
    scrape_roxy()
