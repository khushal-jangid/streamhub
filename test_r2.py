import urllib.request
import time

r2_url = "https://1e944b5039411ad4aa3552df9a08743d.r2.cloudflarestorage.com/hub/The.Avengers.2012.1080p.BluRay.Hindi.English.DD.5.1.x264.ESubs.mkv"

print("1. Testing R2 range bytes=500000000-505242880 (seek 500MB)...")
t0 = time.time()
req = urllib.request.Request(r2_url, headers={'Range': 'bytes=500000000-505242880', 'User-Agent': 'Mozilla/5.0'})
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        print("Status:", resp.status)
        print("Content-Range:", resp.headers.get('Content-Range'))
        data = resp.read()
    t1 = time.time()
    mb = len(data) / (1024 * 1024)
    duration = t1 - t0
    print(f"R2 Speed: {mb:.2f} MB in {duration:.2f}s ({mb/duration:.2f} MB/s = {mb*8/duration:.2f} Mbps)")
except Exception as e:
    print("Error:", e)
