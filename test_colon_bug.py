import urllib.request
from app import resolve_hubcloud

res = resolve_hubcloud("https://hubcloud.ist/drive/cvf2qwgvkpcfkcq")
seek_url = res.get('seek_url')
print("Current seek_url in app:", seek_url)

# Replace %3A%3A with literal ::
literal_seek_url = seek_url.replace('%3A%3A', '::').replace('%3a%3a', '::')
print("Literal seek_url:       ", literal_seek_url)

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://gamerxyt.com/',
    'Range': 'bytes=100000000-101000000' # 1 MB range
}

print("\n1. Testing with literal :: ...")
try:
    req = urllib.request.Request(literal_seek_url, headers=headers)
    with urllib.request.urlopen(req, timeout=8) as resp:
        print("Literal :: Status:", resp.status)
        print("Content-Range:", resp.headers.get('Content-Range'))
        d = resp.read(1024)
        print("Read bytes:", len(d))
except Exception as e:
    print("Literal :: Error:", e)

print("\n2. Testing with %3A%3A ...")
try:
    req = urllib.request.Request(seek_url, headers=headers)
    with urllib.request.urlopen(req, timeout=8) as resp:
        print("%3A%3A Status:", resp.status)
except Exception as e:
    print("%3A%3A Error:", e)
