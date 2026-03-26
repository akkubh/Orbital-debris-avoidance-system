
import requests

# Modern CelesTrak GP API URLs
# 'active' gets all currently functioning satellites
ACTIVE_SATS_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=tle"
# 'iridium-33-debris' is a common group for large debris fields (like the Iridium 33 debris)
DEBRIS_TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=iridium-33-debris&FORMAT=tle"

def fetch_tles(url):
    # 1. Define the headers (to look like a browser)
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    try:
        # 2. Make the actual request
        res = requests.get(url, headers=headers, timeout=15)
        
        # 3. ADD THE PRINT HERE (Right after the request)
        print(f"DEBUG: Fetching from {url} - Status: {res.status_code}")

        # 4. Check if it was successful (200 OK)
        if res.status_code != 200:
            return []

        lines = res.text.strip().splitlines()
        
        if len(lines) < 3:
            return []

        objects = []
        for i in range(0, len(lines) - 2, 3):
            name = lines[i].strip()
            line1 = lines[i+1].strip()
            line2 = lines[i+2].strip()
            objects.append({"name": name, "line1": line1, "line2": line2})
            
        return objects

    except Exception as e:
        print(f"CRITICAL ERROR in fetcher: {e}")
        return []

def fetch_debris_tles():
    return fetch_tles(DEBRIS_TLE_URL)

def fetch_satellite_tles():
    return fetch_tles(ACTIVE_SATS_URL)