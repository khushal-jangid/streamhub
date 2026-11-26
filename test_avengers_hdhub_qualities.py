from app import parse_movie_details

url = "https://new10.hdhub4u.cl/the-avengers-2012-bluray-hindi-org-5-1-englishdual-audio-x264-full-movie/"
d = parse_movie_details(url)
print("Title:", d.get('title'))
print("Qualities found:", len(d.get('qualities', [])))
for q in d.get('qualities', []):
    name = q.get('title', '').encode('ascii', errors='ignore').decode('ascii')
    print(" -", name)
    print("   Link:", q.get('link'))
