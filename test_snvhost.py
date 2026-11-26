import urllib.request

url = "https://snvhost.com/"
try:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=5) as resp:
        print("snvhost status:", resp.status)
        print("html:", resp.read(500))
except Exception as e:
    print("snvhost error:", e)
