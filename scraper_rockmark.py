import cloudscraper
import urllib3
import csv
import os
import time
import html

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SITE_URL = "https://www.rockmark.hr"
CSV_FILENAME = "rockmark_ploce.csv"

API_PRODUCTS_URL = f"{SITE_URL}/wp-json/wc/store/v1/products"
KATEGORIJA_ID = "777"  # ID kategorije "CD / DVD / LP"

def kreiraj_sesiju():
    s = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "hr-HR,hr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive"
    })
    return s

def ocisti_naziv(sirovi_naslov):
    cisti = html.unescape(sirovi_naslov)
    cisti = cisti.replace("–", "-").replace("—", "-")
    return " ".join(cisti.split()).strip()

def je_li_vinil(naslov):
    """ Filtar koji iz mješovite kategorije propušta samo ploče """
    n_up = naslov.upper()
    
    # Odbacujemo očite CD-ove i DVD-ove
    if " CD" in n_up or " DVD" in n_up or "(CD)" in n_up or "BLU-RAY" in n_up:
        return False
        
    # Tražimo ključne oznake za vinil
    oznake_vinila = [" LP", "-LP", "(LP)", "VINYL", "VINIL", " PLOČA", " PLOCA", " 2LP", " 3LP", ' 7"', ' 12"']
    if any(x in n_up for x in oznake_vinila):
        return True
        
    return False

def odredi_tip_artikla(naziv):
    n_up = naziv.upper()
    if any(oznaka in n_up for oznaka in [' 7"', " 7''", " 7″", " EP", " SINGLE", ' 10"']):
        return 'Singl 7"'
    return "LP"

def scrape_rockmark():
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
        print(f"📦 Učitano {len(sve_ploce)} postojećih zapisa iz {CSV_FILENAME}.", flush=True)

    sesija = kreiraj_sesiju()
    print(f"\n🚀 Pokrećem API dohvat Rockmark kataloga (Kat. ID: {KATEGORIJA_ID})...", flush=True)

    ukupno_stranica = 1
    trenutna_stranica = 1
    params = {"per_page": 100, "page": 1, "category": KATEGORIJA_ID}
    dodano_vinila = 0

    while trenutna_stranica <= ukupno_stranica:
        params["page"] = trenutna_stranica
        t_page = time.time()
        
        try:
            r = sesija.get(API_PRODUCTS_URL, params=params, timeout=20)
            
            if r.status_code != 200:
                print(f"   ❌ Stranica {trenutna_stranica} vratila grešku {r.status_code}. Prekidam.", flush=True)
                break
                
            if trenutna_stranica == 1 and 'X-WP-TotalPages' in r.headers:
                ukupno_stranica = int(r.headers['X-WP-TotalPages'])
                total_items = r.headers.get('X-WP-Total', '?')
                print(f"📂 API JAVLJA: Ukupno {total_items} artikala (CD/DVD/LP mješavina) kroz {ukupno_stranica} stranica.", flush=True)

            proizvodi = r.json()
            if not proizvodi:
                break

            dodano_na_stranici = 0
            
            for p in proizvodi:
                if not p.get("is_purchasable", True) or not p.get("is_in_stock", True):
                    continue

                naslov = ocisti_naziv(p.get("name", ""))
                
                if not je_li_vinil(naslov):
                    continue

                cijena = "0.00"
                cijene_blok = p.get("prices", {})
                if cijene_blok:
                    raw_cijena = cijene_blok.get("price", "0")
                    minor_unit = int(cijene_blok.get("currency_minor_unit", 2))
                    if str(raw_cijena).isdigit() and int(raw_cijena) > 0:
                        cijena = f"{int(raw_cijena) / (10 ** minor_unit):.2f}"

                if cijena == "0.00":
                    continue

                link = p.get("permalink", "")
                if not link:
                    continue
                
                slika_url = ""
                slike = p.get("images", [])
                if slike and isinstance(slike, list):
                    slika_url = slike[0].get("src", "")

                tip_artikla = odredi_tip_artikla(naslov)
                stanje = "Novo"

                red = [naslov, cijena, link, slika_url, stanje, stanje, tip_artikla]
                sve_ploce[link] = red
                aktivni_linkovi.add(link)
                dodano_na_stranici += 1
                dodano_vinila += 1

            print(f"   ⚡ Stranica {trenutna_stranica}/{ukupno_stranica} obrađena za {time.time() - t_page:.1f}s | Odvojeno vinila: {dodano_na_stranici}", flush=True)
            trenutna_stranica += 1
            time.sleep(1)
            
        except Exception as e:
            print(f"   ⚠️ Mrežna greška na stranici {trenutna_stranica}: {e}", flush=True)
            time.sleep(3)
            trenutna_stranica += 1

    if len(aktivni_linkovi) > 5:
        pocetni_broj = len(sve_ploce)
        sve_ploce = {k: v for k, v in sve_ploce.items() if k in aktivni_linkovi}
        obrisano = pocetni_broj - len(sve_ploce)
        print(f"\n🧹 Sinkronizacija završena! Uklonjeno {obrisano} rasprodanih artikala iz stare baze.", flush=True)
    else:
        print(f"\n⚠️ Oprez: Vraćen vrlo mali broj artikala ({len(aktivni_linkovi)}). Preskačem brisanje stare baze.", flush=True)

    if sve_ploce:
        with open(CSV_FILENAME, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Naslov", "Cijena", "URL_Proizvoda", "URL_Slike", "Stanje_Medija", "Stanje_Omota", "Tip_Artikla"])
            writer.writerows(sve_ploce.values())
        ukupno_slika = sum(1 for v in sve_ploce.values() if len(v) >= 4 and v[3])
        print(f"🎉 GOTOVO za {time.time() - t_start:.1f}s! U bazi je {len(sve_ploce)} Rockmark ploča (Sa slikom: {ukupno_slika}).", flush=True)

if __name__ == "__main__":
    scrape_rockmark()
