import urllib.request
import time
from app import resolve_hdstream4u

m3u8_url = resolve_hdstream4u("https://hdstream4u.com/file/7p6wwbukw25o")
print("M3U8 URL:", m3u8_url)

# Read playlist to get first .ts segment
req = urllib.request.Request(m3u8_url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://hdstream4u.com/'})
with urllib.request.urlopen(req, timeout=5) as resp:
    pl = resp.read().decode('utf-8')

import re
sub_m3u8 = re.findall(r'https?://[^\s]+\.m3u8[^\s]*', pl)
if sub_m3u8:
    req2 = urllib.request.Request(sub_m3u8[0], headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://hdstream4u.com/'})
    with urllib.request.urlopen(req2, timeout=5) as resp2:
        sub_pl = resp2.read().decode('utf-8')
    ts_files = re.findall(r'https?://[^\s]+\.ts[^\s]*', sub_pl)
    if ts_files:
        print("First TS segment:", ts_files[0][:80])
        t0 = time.time()
        req3 = urllib.request.Request(ts_files[0], headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://hdstream4u.com/'})
        with urllib.request.urlopen(req3, timeout=5) as resp3:
            data = resp3.read()
        t1 = time.time()
        mb = len(data) / (1024 * 1024)
        print(f"Downloaded TS {mb:.2f} MB in {t1-t0:.2f}s ({mb/(t1-t0):.2f} MB/s = {mb*8/(t1-t0):.2f} Mbps)")
