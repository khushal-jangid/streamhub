import urllib.request

url = "https://video-downloads.googleusercontent.com/ADGPM2lO2XYUt0L3Ew4O2MqT1JRUjIVpLY02v556iJ5Vl0m9nYK8wCkC9w"
# Wait, let's get fresh stream URL from test_3gb.py
from app import resolve_hubcloud
res = resolve_hubcloud("https://hubcloud.ist/drive/gf1qxm6t67686t7")
g_url = res.get('stream_url')

tests = [
    ("Range header bytes=1000-2000", g_url, {'Range': 'bytes=1000-2000'}),
    ("Query range=1000-2000", g_url + "&range=1000-2000", {}),
    ("Query byte=1000", g_url + "&byte=1000", {})
]

for name, u, h in tests:
    try:
        req = urllib.request.Request(u, headers={**h, 'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read(20)
            print(f"{name}: status={resp.status}, cl={resp.headers.get('Content-Length')}, data_hex={data.hex()[:20]}")
    except Exception as e:
        print(f"{name}: Error={e}")
