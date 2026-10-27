import cloudscraper
import urllib3
import csv
import os
import time
import html

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SITE_URL = "https://backstagerecords-zagreb.com"
CSV_FILENAME = "backstage_ploce.csv"

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
    cisti = html.unescape(sirovi_naslov)
    cisti = cisti.replace("–", "-").replace("—", "-")
    return " ".join(cisti.split()).strip()


def odredi_tip_artikla(naziv):
    n_up = naziv.upper()
    if any(oznaka in n_up for oznaka in [' 7"', " 7''", " 7″", " EP", " SINGLE", ' 10"', ' 12" MAXI']):
        if ' 10"' in n_up or ' 12"' in n_up:
             return "LP" 
        return 'Singl 7"'
    return "LP"


def otkrij_id_kategorije_vinila(sesija):
    print("🔎 Tražim ID WooCommerce kategorije za gramofonske ploče...", flush=True)
    try:
        r = sesija.get(f"{API_CATEGORIES_URL}?per_page=100", timeout=15)
        if r.status_code == 200:
            kategorije = r.json()
            for kat in kategorije:
                if "ploč" in kat.get("name", "").lower() or "vinyl" in kat.get("name", "").lower():
                    print(f"✅ Pronađena kategorija: {kat['name']} (ID: {kat['id']})", flush=True)
                    return kat['id']
            for kat in kategorije:
                if "domać" in kat.get("name", "").lower() or "ex yu" in kat.get("name", "").lower():
                    print(f"✅ Pronađena zamjenska kategorija: {kat['name']} (ID: {kat['id']})", flush=True)
                    return kat['id']
    except Exception as e:
        print(f"⚠️ Greška pri API dohvatu kategorija: {e}", flush=True)
    
    print("⚠️ Nije pronađena specifična ID kategorija, povlačim cijeli katalog uz filter ključnih riječi...", flush=True)
    return None


def scrape_backstage():
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
    kategorija_id = otkrij_id_kategorije_vinila(sesija)

    params = {"per_page": 100, "page": 1}
    if kategorija_id:
        params["category"] = str(kategorija_id)

    ukupno_stranica = 1
    trenutna_stranica = 1

    print(f"\n🚀 Pokrećem WooCommerce API dohvat ploča...", flush=True)

    while trenutna_stranica <= ukupno_stranica:
        params["page"] = trenutna_stranica
        t_page = time.time()
        
        try:
            r = sesija.get(API_PRODUCTS_URL, params=params, timeout=20)
            
            if r.status_code != 200:
                print(f"   ❌ Stranica {trenutna_stranica} vratila grešku {r.status_code}. Prekidam dohvat.", flush=True)
                break
                
            if trenutna_stranica == 1 and 'X-WP-TotalPages' in r.headers:
                ukupno_stranica = int(r.headers['X-WP-TotalPages'])
                total_items = r.headers.get('X-WP-Total', '?')
                print(f"📊 API javlja: {total_items} artikala kroz {ukupno_stranica} stranica.", flush=True)

            proizvodi = r.json()
            if not proizvodi:
                break

            dodano_na_stranici = 0
            
            for p in proizvodi:
                naslov = ocisti_naziv(p.get("name", ""))
                naslov_low = naslov.lower()
                
                if not kategorija_id:
                    if any(x in naslov_low for x in ["t-shirt", "majic", "funko pop", "kaset", "cassette", "cd", "casopis"]):
                        if " lp" not in naslov_low and " 7\"" not in naslov_low:
                            continue

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
                
                slika_url = ""
                slike = p.get("images", [])
                if slike and isinstance(slike, list):
                    slika_url = slike[0].get("src", "")

                tip_artikla = odredi_tip_artikla(naslov)
                
                # Izvlačenje stanja (grading) direktno iz API atributa
                stanje_medija = "Novo"
                stanje_omota = "Novo"
                
                atributi = p.get("attributes", [])
                for attr in atributi:
                    tax = attr.get("taxonomy", "")
                    ime = attr.get("name", "").lower()
                    if tax == "pa_grading" or ime == "stanje":
                        terms = attr.get("terms", [])
                        if terms:
                            vrijednost = terms[0].get("name", "").strip()
                            if vrijednost and vrijednost.upper() != "NOVO":
                                dijelovi = vrijednost.split("/")
                                stanje_medija = dijelovi[0].strip()
                                stanje_omota = dijelovi[1].strip() if len(dijelovi) > 1 else stanje_medija
                        break

                if stanje_medija == "Novo" and "rabljeno" in naslov_low:
                    stanje_medija = "Rabljeni"
                    stanje_omota = "Rabljeni"

                red = [naslov, cijena, link, slika_url, stanje_medija, stanje_omota, tip_artikla]
                sve_ploce[link] = red
                aktivni_linkovi.add(link)
                dodano_na_stranici += 1

            print(f"   ⚡ Stranica {trenutna_stranica}/{ukupno_stranica} obrađena za {time.time() - t_page:.1f}s (+{dodano_na_stranici} dostupnih).", flush=True)
            trenutna_stranica += 1
            
        except Exception as e:
            print(f"   ⚠️ Mrežna greška na stranici {trenutna_stranica}: {e}", flush=True)
            time.sleep(3)
            trenutna_stranica += 1

    if len(aktivni_linkovi) > 50:
        pocetni_broj = len(sve_ploce)
        sve_ploce = {k: v for k, v in sve_ploce.items() if k in aktivni_linkovi}
        obrisano = pocetni_broj - len(sve_ploce)
        print(f"\n🧹 Sinkronizacija završena! Uklonjeno {obrisano} rasprodanih artikala.", flush=True)
    else:
        print(f"\n⚠️ Oprez: API je vratio samo {len(aktivni_linkovi)} artikala. Preskačem brisanje starih.", flush=True)

    if sve_ploce:
        with open(CSV_FILENAME, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Naslov", "Cijena", "URL_Proizvoda", "URL_Slike", "Stanje_Medija", "Stanje_Omota", "Tip_Artikla"])
            writer.writerows(sve_ploce.values())
        ukupno_slika = sum(1 for v in sve_ploce.values() if len(v) >= 4 and v[3])
        print(f"🎉 GOTOVO za {time.time() - t_start:.1f}s! U bazi je {len(sve_ploce)} dostupnih ploča (Sa slikom: {ukupno_slika}).", flush=True)

if __name__ == "__main__":
    scrape_backstage()
