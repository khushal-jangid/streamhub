from app import resolve_hubcloud

res = resolve_hubcloud("https://hubcloud.ist/drive/gf1qxm6t67686t7")
print("Title:", res.get('title'))
print("Stream URL:", res.get('stream_url')[:80] if res.get('stream_url') else None)
print("Seek URL:", res.get('seek_url')[:80] if res.get('seek_url') else None)
for m in res.get('mirrors', []):
    name = m.get('name', '').encode('ascii', errors='ignore').decode('ascii')
    print(" -", m.get('type'), m.get('alive'), name)
    print("   URL:", m.get('url')[:80])
