from app import resolve_hubcloud

res = resolve_hubcloud("https://hubcloud.ist/drive/cvf2qwgvkpcfkcq")
print("Title:", res.get('title'))
print("Stream URL:", res.get('stream_url')[:100] if res.get('stream_url') else None)
print("Seek URL:", res.get('seek_url')[:100] if res.get('seek_url') else None)
for m in res.get('mirrors', []):
    print("Mirror:", m.get('type'), m.get('alive'), m.get('name')[:60])
    print("  URL:", m.get('url')[:100])
