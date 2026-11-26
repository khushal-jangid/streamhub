import urllib.request
import re

url = "https://hblinks.lol/archives/89406"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        html = resp.read().decode('utf-8', errors='ignore')
        print("HTML length:", len(html))
        for l in re.findall(r'href=[\'"]([^\'"]+)[\'"]', html):
            if any(k in l for k in ['hubcloud', 'drive', 'download', 'fast', 'stream']):
                print(" -", l)
except Exception as e:
    print("Error:", e)
