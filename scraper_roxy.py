import cloudscraper
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import urllib3
import sys

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SITE_URL = "https://www.cdshop-roxy.com"

def stvori_sesiju():
    s = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "hr-HR,hr;q=0.9,en-US;q=0.8,en;q=0.7",
    })
    return s

def izvidnica_roxy():
    s = stvori_sesiju()

    print(f"🔎 [1/3] Spajam se na naslovnicu: {SITE_URL} ...", flush=True)
    try:
        r = s.get(SITE_URL, timeout=30)
    except Exception as e:
        print(f"❌ Greška pri spajanju na naslovnicu: {e}", flush=True)
        sys.exit(1)

    print(f"✅ Naslovnica dohvaćena! HTTP {r.status_code} | Duljina: {len(r.content)} znakova", flush=True)
    # Stare hrvatske stranice obično koriste windows-1250, bs4 to automatski prepoznaje iz meta taga
    soup = BeautifulSoup(r.content, "lxml")

    linkovi_kataloga = []
    vidjeni = set()

    for a in soup.select("a[href]"):
        href = a["href"].strip()
        # Tražimo linkove koji završavaju na .htm (a.htm, b.htm itd.)
        if href.endswith(".htm") or href.endswith(".html"):
            puni_url = urljoin(SITE_URL, href)
            if puni_url not in vidjeni and not href.startswith("http"):
                vidjeni.add(puni_url)
                linkovi_kataloga.append(puni_url)
    
    print(f"\n🎵 === PRONAĐENI LINKOVI KATALOGA ({len(linkovi_kataloga)} komada) ===", flush=True)
    for l in linkovi_kataloga[:10]:
        print(f"  * {l}", flush=True)
    if len(linkovi_kataloga) > 10:
        print("  * ... (i ostala slova abecede)")

    # Testirat ćemo odmah prvo slovo abecede da vidimo strukturu tablice
    test_url = urljoin(SITE_URL, "a.htm")
    print(f"\n🔎 [2/3] Otvaram testnu stranicu kataloga: {test_url}", flush=True)
    
    try:
        r_kat = s.get(test_url, timeout=30)
        print(f"✅ Katalog dohvaćen! HTTP {r_kat.status_code}", flush=True)
        soup_kat = BeautifulSoup(r_kat.content, "lxml")
    except Exception as e:
        print(f"❌ Greška pri spajanju na katalog: {e}", flush=True)
        return

    print(f"\n📦 === ANALIZA HTML TABLICE (Redovi) ===", flush=True)
    redovi = soup_kat.select("tr")
    print(f"Pronađeno {len(redovi)} redova na stranici.\n", flush=True)
    
    ispisano = 0
    for tr in redovi:
        tdovi = tr.select("td")
        # Nas zanimaju samo redovi u kojima pišu artikli (obično imaju 3 do 5 stupaca)
        if len(tdovi) >= 3:
            tekst_reda = tr.get_text(" | ", strip=True)
            # Preskačemo prazne redove
            if len(tekst_reda) > 5:
                print(f"--- KARTICA/RED #{ispisano+1} (Tekstualno) ---")
                print(tekst_reda)
                print("--- HTML KOD REDA ---")
                print(tr.prettify()[:800])
                print("-" * 50)
                ispisano += 1
        
        if ispisano >= 4:
            break
            
    print("\n🔎 [3/3] Izvidnica završena!", flush=True)

if __name__ == "__main__":
    izvidnica_roxy()
