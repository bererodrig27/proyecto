"""Descubrimiento de URLs de notas: RSS por etiqueta, búsqueda WordPress y URLs manuales."""
import json
from urllib.parse import quote

import feedparser

from . import config
from .descargar import descargar


def urls_de_feed(nombre: str, url_feed: str):
    urls = []
    for pagina in range(1, config.PAGINAS_POR_FEED + 1):
        sep = "&" if "?" in url_feed else "?"
        url = url_feed if pagina == 1 else f"{url_feed}{sep}paged={pagina}"
        print(f"Feed {nombre} (pág. {pagina})")
        contenido = descargar(url)
        if not contenido:
            break
        entradas = feedparser.parse(contenido).entries
        if not entradas:
            break
        urls += [(nombre, e.link) for e in entradas if "link" in e]
    return urls


def urls_de_busqueda(nombre: str, base: str, termino: str):
    """API wp-json; si falla, RSS de búsqueda."""
    urls = []
    for pagina in range(1, config.PAGINAS_POR_BUSQUEDA + 1):
        print(f"Búsqueda '{termino}' en {nombre} (pág. {pagina})")
        api = (f"{base}/wp-json/wp/v2/posts?search={quote(termino)}"
               f"&per_page=50&page={pagina}&_fields=link")
        texto = descargar(api)
        if texto:
            try:
                urls += [(nombre, p["link"]) for p in json.loads(texto)]
                continue
            except (ValueError, KeyError, TypeError):
                pass
        rss = f"{base}/?s={quote(termino)}&feed=rss2&paged={pagina}"
        texto = descargar(rss)
        entradas = feedparser.parse(texto).entries if texto else []
        if not entradas:
            break
        urls += [(nombre, e.link) for e in entradas if "link" in e]
    return urls


def cargar_procesadas() -> set:
    if not config.PROCESADAS.exists():
        return set()
    with config.PROCESADAS.open(encoding="utf-8") as f:
        return {l.strip() for l in f if l.strip()}


def marcar_procesada(url: str):
    with config.PROCESADAS.open("a", encoding="utf-8") as f:
        f.write(url + "\n")


def descubrir(ya_procesadas=None):
    """Devuelve [(fuente, url)] nuevas y únicas, con tope por corrida.

    `ya_procesadas` es el conjunto de URLs a saltear; con base de datos lo arma
    guardar.urls_ya_procesadas() a partir de la tabla noticia. Si no se pasa, se usa
    el archivo de texto (modo sin base de datos).
    """
    candidatas = []
    for nombre, url_feed in config.FEEDS.items():
        candidatas += urls_de_feed(nombre, url_feed)
    for nombre, base in config.SITIOS_WP.items():
        for termino in config.TERMINOS_BUSQUEDA:
            candidatas += urls_de_busqueda(nombre, base, termino)
    candidatas += [("Manual", u) for u in config.URLS_MANUALES]

    ya = cargar_procesadas() if ya_procesadas is None else ya_procesadas
    unicas, vistas = [], set()
    for fuente, url in candidatas:
        # noticia.url admite hasta 500 caracteres
        if url not in ya and url not in vistas and len(url) <= 500:
            vistas.add(url)
            unicas.append((fuente, url))
    print(f"\nURLs encontradas: {len(candidatas)} | nuevas y únicas: {len(unicas)}")
    if len(unicas) > config.MAX_NOTAS_POR_CORRIDA:
        print("Se alcanzó el tope por corrida: volvé a ejecutar para seguir con las que faltan.")
    return unicas[: config.MAX_NOTAS_POR_CORRIDA]