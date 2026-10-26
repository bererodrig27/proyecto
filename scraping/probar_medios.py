"""Prueba qué medios sirven para el scraping (no necesita Django ni la base de datos).

Uso (desde la carpeta de manage.py):   python scraping/probar_medios.py
Para cada medio comprueba si tiene la API de WordPress (wp-json) y qué feeds RSS
devuelven notas, y al final imprime las líneas listas para pegar en config.py.
Edite CANDIDATOS para probar otros medios (el dominio de los marcados con ? no está verificado).
"""
import json

import feedparser
import requests

CANDIDATOS = {
    "Primera Edición": "https://www.primeraedicion.com.ar",
    "La Voz de Misiones": "https://www.lavozdemisiones.com",
    "Noticias Ambientales": "https://noticiasambientales.com",
    "El Territorio": "https://www.elterritorio.com.ar",
    "Misiones Cuatro (?)": "https://misionescuatro.com",
    "Canal 12 Misiones (?)": "https://canal12misiones.com",
    "Noticias del 6 (?)": "https://noticiasdel6.com",
}
RUTAS_FEED = [
    "/feed/", "/rss", "/rss/",
    "/tag/fauna/feed/", "/etiqueta/fauna/feed/", "/nota/tag/fauna/feed/", "/category/fauna/feed/",
    "/tag/yaguarete/feed/", "/etiqueta/yaguarete/feed/",
    "/tag/misiones/feed/", "/etiqueta/misiones/feed/",
]
H = {"User-Agent": "TPI-Biodiversidad-Misiones/0.2 (prueba de medios)"}


def get(url):
    try:
        return requests.get(url, headers=H, timeout=15)
    except requests.RequestException as e:
        print(f"    (sin respuesta: {type(e).__name__})")
        return None


def tiene_wp(base):
    r = get(f"{base}/wp-json/wp/v2/posts?search=yaguaret%C3%A9&per_page=3&_fields=link")
    if r is not None and r.ok:
        try:
            return len(json.loads(r.text)) > 0
        except ValueError:
            return False
    return False


def main():
    feeds_ok, wp_ok = [], []
    for nombre, base in CANDIDATOS.items():
        print(f"\n== {nombre}  {base}")
        if tiene_wp(base):
            print("  wp-json: OK (sirve para SITIOS_WP)")
            wp_ok.append((nombre, base))
        else:
            print("  wp-json: no")
        for ruta in RUTAS_FEED:
            r = get(base + ruta)
            n = len(feedparser.parse(r.text).entries) if r is not None and r.ok else 0
            if n:
                print(f"  feed {ruta}: {n} entradas")
                feeds_ok.append((f"{nombre} {ruta}", base + ruta))

    print("\n" + "=" * 60 + "\nPegar en config.py lo que corresponda:\n")
    print("SITIOS_WP = {")
    for nombre, base in wp_ok:
        print(f'    "{nombre}": "{base}",')
    print("}\n\nFEEDS = {   # preferí los de etiqueta de fauna a los generales")
    for nombre, url in feeds_ok:
        print(f'    "{nombre}": "{url}",')
    print("}")


if __name__ == "__main__":
    main()
