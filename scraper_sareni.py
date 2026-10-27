import cloudscraper
import urllib3
import csv
import os
import time
import html
import re

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SITE_URL = "https://sareni-ducan.hr"
CSV_FILENAME = "sareni_ploce.csv"

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


def odredi_tip_artikla(naziv, opisi=""):
    kombinirano = (naziv + " " + opisi).upper()
    if any(oznaka in kombinirano for oznaka in [' 7"', " 7''", " 7″", " EP ", " SINGLE ", ' 10"']):
        return 'Singl 7"'
    return "LP"


def otkrij_vinil_kategorije(sesija):
    print("🔎 Pretražujem API za kategorije gramofonskih ploča na Šarenom Dućanu...", flush=True)
    vinil_kategorije = {}
    try:
        r = sesija.get(f"{API_CATEGORIES_URL}?per_page=100", timeout=15)
        if r.status_code == 200:
            kategorije = r.json()
            for kat in kategorije:
                ime = kat.get("name", "")
                slug = kat.get("slug", "").lower()
                
                # Tražimo isključivo kategorije s pločama (izbjegavamo knjige, stripove, CD-ove)
                if "ploč" in ime.lower() or "ploce" in slug:
                    vinil_kategorije[kat['id']] = ime
                    print(f"  ✅ Pronađena kategorija: {ime} (ID: {kat['id']})", flush=True)
    except Exception as e:
        print(f"⚠️ Greška pri API dohvatu kategorija: {e}", flush=True)
    
    return vinil_kategorije


def izvuci_autora_i_stanje(atributi):
    autor = ""
    stanje_medija = ""
    stanje_omota = ""
    
    for attr in atributi:
        tax = attr.get("taxonomy", "")
        ime = attr.get("name", "").lower()
        terms = attr.get("terms", [])
        
        if not terms:
            continue
            
        vrijednost = terms[0].get("name", "").strip()
        
        # Izvlačenje izvođača (autora)
        if tax == "pa_autor" or ime == "autor" or ime == "izvođač":
            autor = vrijednost
            
        # Ako koriste atribute za stanje rabljenih ploča
        if tax == "pa_grading" or ime == "stanje" or ime == "očuvanost":
            if vrijednost.upper() != "NOVO":
                dijelovi = vrijednost.split("/")
                stanje_medija = dijelovi[0].strip()
                stanje_omota = dijelovi[1].strip() if len(dijelovi) > 1 else stanje_medija

    return autor, stanje_medija, stanje_omota


def odredi_stanje_iz_teksta(naziv, opis, kat_ime):
    # Ako je u kategoriji "Naša izdanja", garantirano je novo
    if "naša izdanja" in kat_ime.lower():
        return "Novo", "Novo"
        
    kombinirano = (naziv + " " + opis).lower()
    
    # Detekcija klasika za second-hand
    if any(x in kombinirano for x in ["rabljeno", "polovno", "second hand"]):
        return "Rabljeni", "Rabljeni"
        
    # Detekcija ocjena (npr. VG+, NM) u tekstu, izbjegavamo lažne pozitivne s granicama riječi
    if re.search(r"\b(vg\+|vg|nm|ex|mint|m-|g\+)\b", kombinirano):
        return "Rabljeni", "Rabljeni"
        
    return "Novo", "Novo"


def scrape_sareni():
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
    kategorije_mapa = otkrij_vinil_kategorije(sesija)
    
    if not kategorije_mapa:
        print("❌ Nisam uspio detektirati nijednu vinil kategoriju iz API-ja! Prekidam rad.", flush=True)
        return

    print(f"\n🚀 Pokrećem WooCommerce API dohvat ploča po kategorijama...", flush=True)

    for kat_id, kat_ime in kategorije_mapa.items():
        ukupno_stranica = 1
        trenutna_stranica = 1
        params = {"per_page": 100, "page": 1, "category": str(kat_id)}

        while trenutna_stranica <= ukupno_stranica:
            params["page"] = trenutna_stranica
            t_page = time.time()
            
            try:
                r = sesija.get(API_PRODUCTS_URL, params=params, timeout=20)
                
                if r.status_code != 200:
                    print(f"   ❌ Kategorija '{kat_ime}', Stranica {trenutna_stranica} vratila grešku {r.status_code}. Preskačem.", flush=True)
                    break
                    
                if trenutna_stranica == 1 and 'X-WP-TotalPages' in r.headers:
                    ukupno_stranica = int(r.headers['X-WP-TotalPages'])
                    total_items = r.headers.get('X-WP-Total', '?')
                    print(f"📂 KATEGORIJA: {kat_ime} -> Ukupno: {total_items} artikala kroz {ukupno_stranica} stranica.", flush=True)

                proizvodi = r.json()
                if not proizvodi:
                    break

                dodano_na_stranici = 0
                
                for p in proizvodi:
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
                    
                    sirovi_naslov = ocisti_naziv(p.get("name", ""))
                    opis = html.unescape(p.get("short_description", "") + " " + p.get("description", ""))
                    
                    slika_url = ""
                    slike = p.get("images", [])
                    if slike and isinstance(slike, list):
                        slika_url = slike[0].get("src", "")

                    # Analiza atributa (Autor i Stanje ako postoji u tax)
                    atributi = p.get("attributes", [])
                    autor, stanje_m_attr, stanje_o_attr = izvuci_autora_i_stanje(atributi)
                    
                    # Formiranje punog naslova (Izvođač - Album)
                    puni_naslov = f"{autor} - {sirovi_naslov}" if autor else sirovi_naslov
                    
                    tip_artikla = odredi_tip_artikla(sirovi_naslov, opis)
                    
                    # Određivanje stanja (ako nije zadano kroz atribut, analiziraj tekst)
                    if stanje_m_attr:
                        stanje_medija, stanje_omota = stanje_m_attr, stanje_o_attr
                    else:
                        stanje_medija, stanje_omota = odredi_stanje_iz_teksta(sirovi_naslov, opis, kat_ime)

                    red = [puni_naslov, cijena, link, slika_url, stanje_medija, stanje_omota, tip_artikla]
                    sve_ploce[link] = red
                    aktivni_linkovi.add(link)
                    dodano_na_stranici += 1

                print(f"   ⚡ Stranica {trenutna_stranica}/{ukupno_stranica} obrađena za {time.time() - t_page:.1f}s (+{dodano_na_stranici} dostupnih).", flush=True)
                trenutna_stranica += 1
                time.sleep(1)
                
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
        print(f"🎉 GOTOVO za {time.time() - t_start:.1f}s! U bazi je {len(sve_ploce)} dostupnih ploča (Sa slikom: {ukupno_slika}).", flush=True)


if __name__ == "__main__":
    scrape_sareni()
