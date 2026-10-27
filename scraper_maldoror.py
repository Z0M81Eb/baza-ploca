import cloudscraper
import urllib3
import csv
import os
import time
import html
import re

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SITE_URL = "https://maldoror.hr"
CSV_FILENAME = "maldoror_ploce.csv"

API_PRODUCTS_URL = f"{SITE_URL}/wp-json/wc/store/v1/products"
API_CATEGORIES_URL = f"{SITE_URL}/wp-json/wc/store/v1/products/categories"


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
    """ Uklanja HTML entitete (poput &#8211;) i čisti razmake """
    cisti = html.unescape(sirovi_naslov)
    cisti = cisti.replace("–", "-").replace("—", "-")
    return " ".join(cisti.split()).strip()


def odredi_tip_artikla(naziv):
    """ Određuje je li LP ili Singl prema nazivu """
    n_up = naziv.upper()
    if any(oznaka in n_up for oznaka in [' 7"', " 7''", " 7″", " EP ", " SINGLE ", ' 10"', ' 12" MAXI']):
        if ' 10"' in n_up or ' 12"' in n_up:
            return "LP"
        return 'Singl 7"'
    return "LP"


def otkrij_vinil_kategorije(sesija):
    print("🔎 Pretražujem API za kategorije gramofonskih ploča na Maldororu...", flush=True)
    vinil_kategorije = []
    try:
        r = sesija.get(f"{API_CATEGORIES_URL}?per_page=100", timeout=15)
        if r.status_code == 200:
            kategorije = r.json()
            for kat in kategorije:
                ime = kat.get("name", "").lower()
                slug = kat.get("slug", "").lower()
                # Maldoror ima jasno izdvojene ploče: 'metal ploče' i 'rock, pop, indie ploče'
                if "ploče" in ime or "ploce" in slug or "vinyl" in ime or "lp" in ime.split():
                    # Ignoriraj prazne roditeljske menije i cd-ove
                    if "cd" not in ime:
                        vinil_kategorije.append(kat['id'])
                        print(f"  ✅ Pronađena vinil kategorija: {kat['name']} (ID: {kat['id']})", flush=True)
    except Exception as e:
        print(f"⚠️ Greška pri API dohvatu kategorija: {e}", flush=True)
    
    return vinil_kategorije


def scrape_maldoror():
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
    kategorije_ids = otkrij_vinil_kategorije(sesija)
    
    if not kategorije_ids:
        print("❌ Nisam uspio detektirati nijednu vinil kategoriju iz API-ja! Prekidam rad.", flush=True)
        return

    print(f"\n🚀 Pokrećem WooCommerce API dohvat ploča po kategorijama...", flush=True)

    for kat_id in kategorije_ids:
        ukupno_stranica = 1
        trenutna_stranica = 1
        params = {"per_page": 100, "page": 1, "category": str(kat_id)}

        while trenutna_stranica <= ukupno_stranica:
            params["page"] = trenutna_stranica
            t_page = time.time()
            
            try:
                r = sesija.get(API_PRODUCTS_URL, params=params, timeout=20)
                
                if r.status_code != 200:
                    print(f"   ❌ Kategorija {kat_id}, Stranica {trenutna_stranica} vratila grešku {r.status_code}. Preskačem.", flush=True)
                    break
                    
                if trenutna_stranica == 1 and 'X-WP-TotalPages' in r.headers:
                    ukupno_stranica = int(r.headers['X-WP-TotalPages'])
                    total_items = r.headers.get('X-WP-Total', '?')
                    print(f"📂 KATEGORIJA ID {kat_id} -> Ukupno: {total_items} artikala kroz {ukupno_stranica} stranica.", flush=True)

                proizvodi = r.json()
                if not proizvodi:
                    break

                dodano_na_stranici = 0
                
                for p in proizvodi:
                    # Preskoči ako nije na stanju
                    if not p.get("is_purchasable", True) or not p.get("is_in_stock", True):
                        continue

                    cijena = "0.00"
                    cijene_blok = p.get("prices", {})
                    if cijene_blok:
                        raw_cijena = cijene_blok.get("price", "0")
                        minor_unit = int(cijene_blok.get("currency_minor_unit", 2))
                        if raw_cijena.isdigit() and int(raw_cijena) > 0:
                            cijena = f"{int(raw_cijena) / (10 ** minor_unit):.2f}"

                    if cijena == "0.00":
                        continue

                    link = p.get("permalink", "")
                    if not link:
                        continue
                    
                    naslov = ocisti_naziv(p.get("name", ""))
                    
                    slika_url = ""
                    slike = p.get("images", [])
                    if slike and isinstance(slike, list):
                        slika_url = slike[0].get("src", "")

                    tip_artikla = odredi_tip_artikla(naslov)
                    
                    # Maldoror drži nove ploče
                    stanje = "Rabljeni" if any(x in naslov.lower() for x in ["rabljeno", "second hand", "polovno"]) else "Novo"

                    red = [naslov, cijena, link, slika_url, stanje, stanje, tip_artikla]
                    sve_ploce[link] = red
                    aktivni_linkovi.add(link)
                    dodano_na_stranici += 1

                print(f"   ⚡ Stranica {trenutna_stranica}/{ukupno_stranica} obrađena za {time.time() - t_page:.1f}s (+{dodano_na_stranici} dostupnih).", flush=True)
                trenutna_stranica += 1
                time.sleep(1)  # Kratka pauza da ne preopteretimo server
                
            except Exception as e:
                print(f"   ⚠️ Mrežna greška na stranici {trenutna_stranica}: {e}", flush=True)
                time.sleep(3)
                trenutna_stranica += 1

    if len(aktivni_linkovi) > 20:
        pocetni_broj = len(sve_ploce)
        sve_ploce = {k: v for k, v in sve_ploce.items() if k in aktivni_linkovi}
        obrisano = pocetni_broj - len(sve_ploce)
        print(f"\n🧹 Sinkronizacija završena! Uklonjeno {obrisano} rasprodanih artikala.", flush=True)
    else:
        print(f"\n⚠️ Oprez: API je vratio samo {len(aktivni_linkovi)} artikala ukupno. Preskačem brisanje starih zapisa.", flush=True)

    if sve_ploce:
        with open(CSV_FILENAME, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Naslov", "Cijena", "URL_Proizvoda", "URL_Slike", "Stanje_Medija", "Stanje_Omota", "Tip_Artikla"])
            writer.writerows(sve_ploce.values())
        ukupno_slika = sum(1 for v in sve_ploce.values() if len(v) >= 4 and v[3])
        print(f"🎉 GOTOVO za {time.time() - t_start:.1f}s! U bazi je {len(sve_ploce)} dostupnih Maldoror ploča (Sa slikom: {ukupno_slika}).", flush=True)


if __name__ == "__main__":
    scrape_maldoror()
