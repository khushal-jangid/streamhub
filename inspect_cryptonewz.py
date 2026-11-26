import urllib.request
import re

# Fetch gamerxyt HTML fresh
url = "https://hubcloud.ist/drive/cvf2qwgvkpcfkcq"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=10) as resp:
    html = resp.read().decode('utf-8', errors='ignore')
    m = re.search(r'href=["\'](https?://gamerxyt\.com/hubcloud\.php\?[^"\']+)["\']', html)
    gx_url = m.group(1).replace('&amp;', '&')

req2 = urllib.request.Request(gx_url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://hubcloud.ist/'})
with urllib.request.urlopen(req2, timeout=10) as resp2:
    gx_html = resp2.read().decode('utf-8', errors='ignore')
    m_pixel = re.search(r'href=["\'](https?://pixel\.hubcloud\.ist/\?id=[^"\']+)["\']', gx_html)
    pixel_url = m_pixel.group(1)
    print("Pixel URL:", pixel_url)

req3 = urllib.request.Request(pixel_url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://gamerxyt.com/'})
with urllib.request.urlopen(req3, timeout=10) as resp3:
    pixel_final = resp3.geturl()
    print("Pixel redirected to:", pixel_final)
    pixel_html = resp3.read().decode('utf-8', errors='ignore')
    print("HTML length:", len(pixel_html))
    # Look for drive.google.com or id or download links
    for l in re.findall(r'href=[\'"]([^\'"]+)[\'"]', pixel_html):
        print(" - link:", l[:120])
