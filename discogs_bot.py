import os
import csv
import time
import requests
import glob
import urllib.parse

DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN")
MAX_DNEVNI_UPITI = 800  # Ograničenje kako GitHub Action ne bi predugo trajao. 3000 ploča riješit će za 4 dana.

def nadi_sliku(upit):
    if not DISCOGS_TOKEN:
        print("❌ Greška: Nije pronađen DISCOGS_TOKEN u sustavu!")
        return ""
        
    # 'type=master' osigurava da dobijemo samo jednu, krovnu sliku albuma
    url = f"https://api.discogs.com/database/search?q={urllib.parse.quote(upit)}&type=master&token={DISCOGS_TOKEN}"
    headers = {"User-Agent": "TrazimPlocuBot/1.0"}
    
    try:
        r = requests.get(url, headers=headers, timeout=10)
        
        if r.status_code == 429:
            print("⚠️ Discogs Rate Limit pogođen! Čekam 65 sekundi...")
            time.sleep(65)
            return nadi_sliku(upit)
            
        if r.status_code == 200:
            podaci = r.json()
            if podaci.get("results"):
                slika_url = podaci["results"][0].get("cover_image", "")
                # Odbacujemo generičku sivu sliku ako Discogs nema pravi omot
                if "spacer.gif" in slika_url or "default" in slika_url:
                    return ""
                return slika_url
    except Exception as e:
        print(f"Greška mreže za {upit}: {e}")
        
    return ""

def pokreni_bota():
    csv_datoteke = glob.glob("*_ploce.csv")
    upita_potroseno = 0
    
    if not csv_datoteke:
        print("Nema CSV datoteka za analizu.")
        return

    for file in csv_datoteke:
        print(f"\n📂 Otvaram katalog: {file}")
        with open(file, "r", encoding="utf-8") as f:
            reader = list(csv.reader(f))
            
        if len(reader) < 2:
            continue
            
        header = reader[0]
        redovi = reader[1:]
        azurirano = 0
        
        for i, red in enumerate(redovi):
            # Provjera: ima li red barem 4 stupca i je li polje za URL_Slike (indeks 3) prazno
            if len(red) >= 4 and not red[3]:
                # Mijenjamo minus u razmak za lakšu pretragu (npr. "Abba - Greatest Hits" u "Abba Greatest Hits")
                cisti_naslov = red[0].replace("-", " ").strip()
                
                print(f"🔎 Tražim: {cisti_naslov}")
                pronadjena_slika = nadi_sliku(cisti_naslov)
                
                if pronadjena_slika:
                    print(f"  ✅ Nađeno: {pronadjena_slika}")
                    redovi[i][3] = pronadjena_slika
                    azurirano += 1
                else:
                    print("  ❌ Nema rezultata.")
                
                upita_potroseno += 1
                time.sleep(1.5) # Strogo poštivanje Discogs limita od 60 upita u minuti
                
                if upita_potroseno >= MAX_DNEVNI_UPITI:
                    print(f"\n⏸️ Dosegnut dnevni limit od {MAX_DNEVNI_UPITI} upita. Bot pauzira do sutra.")
                    break
        
        if azurirano > 0:
            with open(file, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(header)
                writer.writerows(redovi)
            print(f"💾 Spremljeno {azurirano} novih slika u {file}!")
        else:
            print(f"👍 Sve slike su već bile popunjene ili nije nađeno ništa novo za {file}.")
            
        if upita_potroseno >= MAX_DNEVNI_UPITI:
            break

if __name__ == "__main__":
    pokreni_bota()
