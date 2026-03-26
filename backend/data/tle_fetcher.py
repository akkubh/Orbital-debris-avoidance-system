import requests
import numpy as np

CELESTRAK_URL = "https://celestrak.org/SOCRATES/query.php"
DEBRIS_TLE_URL = "https://celestrak.org/pub/TLE/iridium-33-debris.txt"
ACTIVE_SATS_URL = "https://celestrak.org/pub/TLE/active.txt"

def fetch_tles(url):
    res = requests.get(url, timeout=10)
    lines = res.text.strip().splitlines()
    objects = []
    for i in range(0, len(lines) - 2, 3):
        name = lines[i].strip()
        line1 = lines[i+1].strip()
        line2 = lines[i+2].strip()
        objects.append({"name": name, "line1": line1, "line2": line2})
    return objects

def fetch_debris_tles():
    return fetch_tles(DEBRIS_TLE_URL)

def fetch_satellite_tles():
    return fetch_tles(ACTIVE_SATS_URL)