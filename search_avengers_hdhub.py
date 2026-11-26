from app import search_hdhub4u

results = search_hdhub4u("The Avengers")
for r in results:
    title = r.get('title', '').encode('ascii', errors='ignore').decode('ascii')
    print(" -", title, r.get('permalink'))
