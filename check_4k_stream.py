import urllib.request
import re

url = "https://4khdhub.one/the-avengers-marvel-phase-1-movie-145/"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=10) as resp:
    html = resp.read().decode('utf-8', errors='ignore')
    print("Links on 4khdhub:")
    for l in re.findall(r'href=[\'"]([^\'"]+)[\'"]', html):
        if any(k in l for k in ['stream', 'watch', 'm3u8', 'play', 'online', 'fast']):
            print(" -", l)
