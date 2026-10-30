import os
import csv
import time
import requests
import glob
import urllib.parse
import re

# =========================================================================
# ⚙️ POSTAVKE DISCOGS BOTA
# =========================================================================
DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN")
MAX_DNEVNI_UPITI = 800  # Globalni limit po jednom pokretanju
FALLBACK_SLIKA_URL = "https://tvoja-domena.hr/wp-content/uploads/fallback-ploca.jpg" # <-- OVDJE UPIŠI URL SVOJE FALLBACK SLIKE
# =========================================================================

def pametno_ciscenje(naslov, obrni_ime=False):
    # Prvo razdvajamo izvođača i album pomoću crtice
    dijelovi = naslov.split("-", 1)
    
    if len(dijelovi) == 2:
        izvodjac = dijelovi[0].strip()
        album = dijelovi[1].strip()
        
        # Pametni Swap: Okrećemo prve dvije riječi (npr. "SPRINGSTEEN BRUCE" u "BRUCE SPRINGSTEEN")
        if obrni_ime:
            izv_rijeci = izvodjac.split()
            if len(izv_rijeci) == 2:
                izvodjac = f"{izv_rijeci[1]} {izv_rijeci[0]}"
                
        cisti = f"{izvodjac} {album}"
    else:
        cisti = naslov.replace("-", " ")
        
    # Uklanjanje sufiksa s formata: ...LP, ..LP2, LP6, LP10 (Zadržavamo naslov čistim)
    cisti = re.sub(r'(?i)\.{1,3}\s*LP\d*\b', ' ', cisti) 
    cisti = re.sub(r'(?i)\bLP\d*\b', ' ', cisti)        
    
    # Uklanjanje opisa boja i vinila
    cisti = re.sub(r'(?i)\b(white|black|red|blue|green|yellow|colored|coloured|remastered|clear|180g)\s+vinyl\b', ' ', cisti)
    cisti = re.sub(r'(?i)\b(remastered|ost)\b', ' ', cisti)
    
    # Uklanjanje svega u zagradama
    cisti = re.sub(r'\(.*?\)', ' ', cisti)
    
    # Brisanje suvišnih razmaka
    return re.sub(r'\s+', ' ', cisti).strip()

def nadi_sliku(upit):
    if not DISCOGS_TOKEN:
        print("❌ Greška: Nije pronađen DISCOGS_TOKEN u sustavu!")
        return None
        
    url = f"https://api.discogs.com/database/search?q={urllib.parse.quote(upit)}&type=master&token={DISCOGS_TOKEN}"
    headers = {"User-Agent": "TrazimPlocuBot/3.0"}
    
    try:
        r = requests.get(url, headers=headers, timeout=12)
        
        if r.status_code == 429:
            print("⚠️ Discogs Rate Limit pogođen! Čekam 65 sekundi...")
            time.sleep(65)
            return nadi_sliku(upit)
            
        if r.status_code == 200:
            podaci = r.json()
            if podaci.get("results"):
                slika_url = podaci["results"][0].get("cover_image", "")
                if "spacer.gif" in slika_url or "default" in slika_url:
                    return ""
                return slika_url
            return "" # Pretraga je uspjela, ali nema rezultata na Discogsu
    except Exception as e:
        print(f"Greška mreže za {upit}: {e}")
        return None # Greška mreže - ne stavljamo fallback da pokuša opet
        
    return None

def pokreni_bota():
    csv_datoteke = glob.glob("*.csv")
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
            if len(red) < 4:
                continue
                
            trenutna_slika = red[3].strip()
            
            # UKLONJENA MJESEČNA PROVJERA -> ZAHTIJEVA HITNI RESET SVIH FALLBACK SLIKA
            treba_traziti = (not trenutna_slika) or (trenutna_slika == FALLBACK_SLIKA_URL)
            
            if treba_traziti:
                # 1. POKUŠAJ: Očišćen redovni naslov (Briše LP2, boje, remastere)
                cisti_naslov = pametno_ciscenje(red[0], obrni_ime=False)
                print(f"🔎 [{upita_potroseno + 1}/{MAX_DNEVNI_UPITI}] Tražim: {cisti_naslov}")
                
                pronadjena_slika = nadi_sliku(cisti_naslov)
                
                # 2. POKUŠAJ: Ako nema, okrećemo izvođača (SPRINGSTEEN BRUCE -> BRUCE SPRINGSTEEN)
                if pronadjena_slika == "" and "-" in red[0]:
                    obrnuti_naslov = pametno_ciscenje(red[0], obrni_ime=True)
                    if obrnuti_naslov != cisti_naslov:
                        print(f"   🔄 Pokušavam ponovno s obrnutim imenom: {obrnuti_naslov}")
                        pronadjena_slika = nadi_sliku(obrnuti_naslov)
                        upita_potroseno += 1
                        time.sleep(1.5)
                
                if pronadjena_slika:
                    print(f"  ✅ Nađeno: {pronadjena_slika}")
                    redovi[i][3] = pronadjena_slika
                    azurirano += 1
                elif pronadjena_slika == "":
                    if trenutna_slika != FALLBACK_SLIKA_URL:
                        print("  ❌ Definitivno nema na Discogsu -> Postavljam Fallback sliku.")
                        redovi[i][3] = FALLBACK_SLIKA_URL
                        azurirano += 1
                    else:
                        print("  ❌ I dalje nema na Discogsu -> Ostaje Fallback slika.")
                
                upita_potroseno += 1
                time.sleep(1.5) # Poštivanje Discogs limita (max 40 u minuti)
                
                if upita_potroseno >= MAX_DNEVNI_UPITI:
                    print(f"\n⏸️ Dosegnut globalni dnevni limit od {MAX_DNEVNI_UPITI} upita. Bot staje za danas.")
                    break
        
        if azurirano > 0:
            with open(file, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(header)
                writer.writerows(redovi)
            print(f"💾 Spremljeno {azurirano} promjena u {file}!")
        else:
            print(f"👍 Nema novih izmjena za {file}.")
            
        if upita_potroseno >= MAX_DNEVNI_UPITI:
            break

if __name__ == "__main__":
    pokreni_bota()
