import requests, feedparser, json
H = {"User-Agent": "TPI-test"}
b = "https://www.primeraedicion.com.ar"

# 1) ¿Existe la API de WordPress?
r = requests.get(b + "/wp-json/wp/v2/posts?search=yaguareté&per_page=5&_fields=link", headers=H, timeout=10)
print("wp-json:", r.status_code)
try:
    print(json.loads(r.text)[:2])
except Exception:
    print("no devolvió JSON")

# 2) ¿Existe el feed de la etiqueta fauna?
for ruta in ["/nota/tag/fauna/feed/", "/nota/tag/fauna/feed", "/tag/fauna/feed/"]:
    r = requests.get(b + ruta, headers=H, timeout=10)
    print(ruta, r.status_code, len(feedparser.parse(r.text).entries), "entradas")