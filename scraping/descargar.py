"""Descarga de páginas (respetando robots.txt) y limpieza del HTML de las notas."""
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
import re 
import requests
from bs4 import BeautifulSoup

from . import config

sesion = requests.Session()
sesion.headers.update({"User-Agent": config.USER_AGENT})
_robots_cache = {}
ESTADISTICAS = {"descargas_ok": 0, "descargas_error": 0}


def permitido_por_robots(url: str) -> bool:
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots_cache:
        rp = None
        try:
            r = sesion.get(base + "/robots.txt", timeout=config.TIMEOUT)
            if r.status_code == 200:
                rp = RobotFileParser()
                rp.parse(r.text.splitlines())
        except requests.RequestException:
            rp = None
        _robots_cache[base] = rp
    rp = _robots_cache[base]
    return True if rp is None else rp.can_fetch(config.USER_AGENT, url)


def descargar(url: str):
    """Devuelve el contenido (texto) o None si falla / no está permitido."""
    if not permitido_por_robots(url):
        print(f"  [robots.txt no permite] {url}")
        return None
    try:
        r = sesion.get(url, timeout=config.TIMEOUT)
        r.raise_for_status()
        ESTADISTICAS["descargas_ok"] += 1
        return r.text
    except requests.RequestException as e:
        ESTADISTICAS["descargas_error"] += 1
        print(f"  [error] {url}: {e}")
        return None
    finally:
        time.sleep(config.PAUSA_SEGUNDOS)



def fecha_publicacion(soup, meta):
    """Fecha de publicación con varios respaldos: metatags, JSON-LD y <time datetime>."""
    f = meta("article:published_time", "og:article:published_time", "datePublished",
             "date", "pubdate", "DC.date.issued")
    if f:
        return f
    for s in soup.find_all("script", type="application/ld+json"):
        m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', s.string or s.get_text() or "")
        if m:
            return m.group(1)
    t = soup.find("time", attrs={"datetime": True})
    return t["datetime"].strip() if t else ""


def extraer_nota(html: str) -> dict:
    """Extrae título, fecha de publicación y texto limpio de una nota."""
    soup = BeautifulSoup(html, "html.parser")

    def meta(*nombres):
        for n in nombres:
            tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
            if tag and tag.get("content"):
                return tag["content"].strip()
        return ""

    titulo = meta("og:title") or (soup.title.string.strip() if soup.title and soup.title.string else "")
    fecha = fecha_publicacion(soup, meta)

    contenedor = soup.find("article") or soup
    for basura in contenedor(["script", "style", "nav", "footer", "aside", "form"]):
        basura.decompose()
    parrafos = [p.get_text(" ", strip=True) for p in contenedor.find_all("p")]
    texto = "\n".join(p for p in parrafos if len(p) > 40)
    return {"titulo": titulo, "fecha": fecha, "texto": texto}