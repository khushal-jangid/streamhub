import urllib.request
import re

url = "https://hubcloud.ist/drive/cvf2qwgvkpcfkcq"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=10) as resp:
    html = resp.read().decode('utf-8', errors='ignore')
    print("Links on hubcloud:")
    for l in re.findall(r'href=[\'"]([^\'"]+)[\'"]', html):
        print(" -", l)
    for src in re.findall(r'src=[\'"]([^\'"]+)[\'"]', html):
        print(" - src:", src)
