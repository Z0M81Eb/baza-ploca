import cloudscraper
import urllib3
import csv
import os
import time
import html

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SITE_URL = "https://www.pdv.com.hr"
CSV_FILENAME = "pdv_ploce.csv"

# Preuzimamo cijeli repozitorij preko glavnog Shopify JSON-a (do 250 artikala po stranici)
API_PRODUCTS_URL = f"{SITE_URL}/products.json"


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


def odredi_tip_artikla(naziv, product_type):
    n_up = (naziv + " " + product_type).upper()
    if any(oznaka in n_up for oznaka in [' 7"', " 7''", " 7″", " EP", " SINGLE", ' 10"']):
        return 'Singl 7"'
    return "LP"


def je_li_vinil(proizvod):
    """Provjera je li Shopify artikl gramofonska ploča"""
    ptype = proizvod.get("product_type", "").lower()
    tags = [t.lower() for t in proizvod.get("tags", [])]
    naslov = proizvod.get("title", "").lower()

    # Odbacujemo sve što je očito kriva kategorija
    if any(x in ptype for x in ["cd", "dvd", "merchandise", "t-shirt", "majica", "hoodie", "kazeta", "digital", "slipmat"]):
        return False

    if any(x in naslov for x in ["cd", "t-shirt", "majica", "slipmat", "kazeta"]):
        return False

    # Ako eksplicitno piše da je LP, 7", 10", 12" ili Vinyl u tipu
    dozvoljeni_tipovi = ["lp", "vinyl", "2lp", "3lp", "4lp", "7", "10", "12", "7\"", "12\""]
    if ptype in dozvoljeni_tipovi:
        return True

    # Ponekad je tip prazan, ali naziv govori da je LP (npr. "Artist - Album - LP")
    if " - lp" in naslov or " - 2lp" in naslov or " - 7\"" in naslov:
        return True

    # Ako je product_type skroz prazan, ali tags sadrže indikatore
    if any(x in tags for x in dozvoljeni_tipovi):
        return True

    return False


def scrape_pdv():
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
    
    print(f"\n🚀 Pokrećem Shopify API dohvat proizvoda...", flush=True)

    trenutna_stranica = 1
    nastavi_dohvat = True
    ukupno_artikala_iz_api = 0
    dodano_vinila = 0

    while nastavi_dohvat:
        t_page = time.time()
        params = {"limit": 250, "page": trenutna_stranica}
        
        try:
            r = sesija.get(API_PRODUCTS_URL, params=params, timeout=20)
            
            if r.status_code != 200:
                print(f"   ❌ Stranica {trenutna_stranica} vratila grešku {r.status_code}. Prekidam.", flush=True)
                break

            data = r.json()
            proizvodi = data.get("products", [])
            
            if not proizvodi:
                nastavi_dohvat = False
                break

            ukupno_artikala_iz_api += len(proizvodi)
            dodano_na_stranici = 0
            
            for p in proizvodi:
                if not je_li_vinil(p):
                    continue

                naslov = ocisti_naziv(p.get("title", ""))
                link = f"{SITE_URL}/products/{p.get('handle', '')}"
                
                # Shopify API uvijek šalje array varijanti za cijenu i zalihu
                varijante = p.get("variants", [])
                if not varijante:
                    continue
                    
                glavna_varijanta = varijante[0]
                
                # PDV prati zalihe na razini varijante, ako 'available' nije true, preskačemo
                if not glavna_varijanta.get("available", True):
                    continue

                cijena_sirovo = glavna_varijanta.get("price", "0")
                try:
                    cijena = f"{float(cijena_sirovo):.2f}"
                except:
                    cijena = "0.00"

                if cijena == "0.00" or float(cijena) == 0:
                    continue

                slika_url = ""
                slike = p.get("images", [])
                if slike:
                    slika_url = slike[0].get("src", "")

                tip_artikla = odredi_tip_artikla(naslov, p.get("product_type", ""))
                
                # PDV (Pozitivan Ritam) prodaje isključivo nove ploče, ali provjerit ćemo za second-hand
                stanje = "Rabljeni" if "second hand" in p.get("tags", []) else "Novo"

                red = [naslov, cijena, link, slika_url, stanje, stanje, tip_artikla]
                sve_ploce[link] = red
                aktivni_linkovi.add(link)
                dodano_vinila += 1
                dodano_na_stranici += 1

            print(f"   ⚡ Stranica {trenutna_stranica} obrađena za {time.time() - t_page:.1f}s | Pročitano: {len(proizvodi)} artikala | Dodano vinila: {dodano_na_stranici}", flush=True)
            trenutna_stranica += 1
            
        except Exception as e:
            print(f"   ⚠️ Mrežna greška na stranici {trenutna_stranica}: {e}", flush=True)
            time.sleep(3)
            trenutna_stranica += 1
            # Prekidi na API iteraciji kod Shopifyja ponekad znače kraj
            if trenutna_stranica > 10:
                nastavi_dohvat = False

    if len(aktivni_linkovi) > 50:
        pocetni_broj = len(sve_ploce)
        sve_ploce = {k: v for k, v in sve_ploce.items() if k in aktivni_linkovi}
        obrisano = pocetni_broj - len(sve_ploce)
        print(f"\n🧹 Sinkronizacija završena! Uklonjeno {obrisano} rasprodanih artikala iz stare baze.", flush=True)
    else:
        print(f"\n⚠️ Oprez: Zadržavam staru bazu jer je povučen sumnjivo mali broj ploča ({len(aktivni_linkovi)}).", flush=True)

    if sve_ploce:
        with open(CSV_FILENAME, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Naslov", "Cijena", "URL_Proizvoda", "URL_Slike", "Stanje_Medija", "Stanje_Omota", "Tip_Artikla"])
            writer.writerows(sve_ploce.values())
        ukupno_slika = sum(1 for v in sve_ploce.values() if len(v) >= 4 and v[3])
        print(f"🎉 GOTOVO za {time.time() - t_start:.1f}s! Ukupno pročitano {ukupno_artikala_iz_api} shop artikala. U bazi je pohranjeno {len(sve_ploce)} dostupnih PDV vinila (Sa slikom: {ukupno_slika}).", flush=True)


if __name__ == "__main__":
    scrape_pdv()
