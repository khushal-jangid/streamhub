import urllib.request
import time
from app import resolve_hubcloud

res = resolve_hubcloud("https://hubcloud.ist/drive/cvf2qwgvkpcfkcq")
seek_url = res.get('seek_url')
print("Seek URL:", seek_url)

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://gamerxyt.com/',
    'Range': 'bytes=100000000-105242880' # 5 MB chunk at 100MB offset
}

t0 = time.time()
req = urllib.request.Request(seek_url, headers=headers)
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        print("Status:", resp.status)
        print("Content-Range:", resp.headers.get('Content-Range'))
        data = resp.read()
    t1 = time.time()
    mb = len(data) / (1024 * 1024)
    duration = t1 - t0
    print(f"Downloaded {mb:.2f} MB in {duration:.2f}s ({mb/duration:.2f} MB/s = {mb*8/duration:.2f} Mbps)")
except Exception as e:
    print("Error:", e)
