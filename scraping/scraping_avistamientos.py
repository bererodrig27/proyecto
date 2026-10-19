"""
Scraper de noticias sobre fauna en Misiones -> guarda avistamientos en la base de datos.

Flujo:
  1. Junta URLs de notas desde tres fuentes:
       a) feeds RSS por etiqueta (fauna, fauna silvestre...) con paginación (?paged=1..N)
       b) búsquedas por especie en sitios WordPress (API wp-json, con RSS de respaldo)
       c) URLs manuales
  2. Descarta duplicados y las URLs ya procesadas en corridas anteriores.
  3. Descarga cada nota y extrae título, fecha y texto.
  4. Detecta especies y lugares (diccionarios) y guarda las relevantes en noticias.csv.
  5. NUEVO: por cada especie detectada crea un `Registro` (avistamiento) en la base de datos:
       - latitud/longitud: tomadas del diccionario COORDENADAS según el lugar detectado
       - fecha_avistamiento: fecha de publicación de la nota (si no hay, ver FECHA_SI_FALTA)
       - estado_calidad: PENDIENTE (lo valida luego un curador)

Instalar:  pip install requests beautifulsoup4 feedparser
Ubicación: colocar este archivo junto a manage.py (o ajustar DJANGO_SETTINGS_MODULE / sys.path).
Ejecutar:  python scraper_noticias.py
"""
import csv
import json
import os
import re
import time
import unicodedata
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote, urlparse
from urllib.robotparser import RobotFileParser

import feedparser
import requests
from bs4 import BeautifulSoup

# ------------------------------------------------------------------ DJANGO
# AJUSTAR: nombre del módulo de settings y de la app donde están los modelos.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.db import transaction  # noqa: E402
from django.core.exceptions import ValidationError  # noqa: E402

from MisionaBio.models import Especie, Fuente, Registro  # noqa: E402  (AJUSTAR nombre de la app)

# ------------------------------------------------------------------ CONFIG
# --- a) Feeds RSS por etiqueta/sección. Se leen varias páginas hacia atrás.
FEEDS = {
    "Misiones Online - fauna": "https://misionesonline.net/etiqueta/fauna/feed/",
    "Misiones Online - fauna silvestre": "https://misionesonline.net/etiqueta/fauna-silvestre/feed/",
    "Misiones Online - fauna misionera": "https://misionesonline.net/etiqueta/fauna-misionera/feed/",
    "Misiones Online - general": "https://misionesonline.net/feed/",
}
PAGINAS_POR_FEED = 10

# --- b) Búsqueda por término en sitios WordPress
SITIOS_WP = {
    "Misiones Online": "https://misionesonline.net",
}
TERMINOS_BUSQUEDA = [
    "yaguareté", "puma", "tapir", "oso hormiguero", "carayá", "tucán",
    "aguará guazú", "yarará", "coatí", "ocelote", "avistamiento fauna",
    "fauna silvestre", "Parque Nacional Iguazú fauna", "Yabotí fauna",
]
PAGINAS_POR_BUSQUEDA = 3

# --- c) URLs sueltas de notas
URLS_MANUALES = []

USER_AGENT = "TPI-Biodiversidad-Misiones/0.1 (proyecto educativo; contacto: tu_mail@ejemplo.com)"
PAUSA_SEGUNDOS = 2
MAX_NOTAS_POR_CORRIDA = 40
SALIDA = Path("noticias.csv")
PROCESADAS = Path("urls_procesadas.txt")

# --- Base de datos
GUARDAR_EN_BD = True            # False = solo CSV, como antes
REQUIERE_ESPECIE_PARA_BD = True  # True: no crea registros si la nota no tiene ninguna especie
                                 # con nombre científico (evita avistamientos "vacíos").
                                 # False: los crea con especie=NULL para que la IA los sugiera.
FECHA_SI_FALTA = "hoy"           # si la nota no trae fecha de publicación: "hoy" o "omitir"
# Campos extra que pida tu tabla `fuente` al crearla (ej. {"url": "...", "tipo": "Prensa"}).
FUENTE_DEFAULTS = {}

# Palabras que indican que la nota habla de un avistamiento/fauna
PALABRAS_CLAVE = [
    "avistamiento", "avistaron", "avistó", "registro", "fauna", "especie",
    "yaguareté", "yaguarete", "jaguar", "puma", "tapir", "oso hormiguero",
    "carayá", "mono", "tucán", "tucan", "loro", "aguará", "aguara", "tortuga",
    "ocelote", "gato", "yarará", "yarara", "coatí", "coati", "vampiro",
]

# Nombre común (minúsculas, sin tildes) -> nombre científico
ESPECIES = {
    "yaguarete": "Panthera onca",
    "jaguar": "Panthera onca",
    "puma": "Puma concolor",
    "tapir": "Tapirus terrestris",
    "anta": "Tapirus terrestris",
    "oso hormiguero": "Myrmecophaga tridactyla",
    "carayá": "Alouatta caraya",
    "caraya": "Alouatta caraya",
    "tucan": "Ramphastos toco",
    "aguara guazu": "Chrysocyon brachyurus",
    "coati": "Nasua nasua",
    "ocelote": "Leopardus pardalis",
    "yarara": "Bothrops alternatus",
    "loro vinoso": "Amazona vinacea",
    "tortuga": None,  # ambiguo: sin científico asignado
}

# Lugares de Misiones a reconocer
LUGARES = [
    "Posadas", "Oberá", "Eldorado", "Puerto Iguazú", "Iguazú", "Apóstoles",
    "San Ignacio", "Montecarlo", "Jardín América", "Leandro N. Alem",
    "San Pedro", "Moconá", "Yabotí", "Cuña Pirú", "Urugua-í", "El Soberbio",
    "Puerto Rico", "Garupá", "Candelaria", "Wanda", "Libertad",
    "Parque Nacional Iguazú", "Parque Provincial Moconá",
    "Reserva de Biosfera Yabotí", "Parque Provincial Urugua-í",
]

# Lugar -> (latitud, longitud). Son coordenadas APROXIMADAS (centro de la localidad o
# del área protegida). Revisarlas/ajustarlas antes de usar los datos en serio.
COORDENADAS = {
    "Posadas": (-27.3671, -55.8961),
    "Oberá": (-27.4871, -55.1199),
    "Eldorado": (-26.4000, -54.6333),
    "Puerto Iguazú": (-25.5991, -54.5736),
    "Iguazú": (-25.5991, -54.5736),
    "Apóstoles": (-27.9086, -55.7555),
    "San Ignacio": (-27.2569, -55.5339),
    "Montecarlo": (-26.5667, -54.7500),
    "Jardín América": (-27.0500, -55.2333),
    "Leandro N. Alem": (-27.6000, -55.3167),
    "San Pedro": (-26.6228, -54.1156),
    "Moconá": (-27.1583, -53.8964),
    "Yabotí": (-26.8000, -54.1000),
    "Cuña Pirú": (-27.0000, -54.8500),
    "Urugua-í": (-25.9000, -54.1500),
    "El Soberbio": (-27.2978, -54.1936),
    "Puerto Rico": (-26.7961, -55.0247),
    "Garupá": (-27.4833, -55.8333),
    "Candelaria": (-27.4631, -55.7436),
    "Wanda": (-25.9700, -54.5700),
    "Libertad": (-25.9000, -54.6000),
    "Parque Nacional Iguazú": (-25.6953, -54.4367),
    "Parque Provincial Moconá": (-27.1500, -53.9000),
    "Reserva de Biosfera Yabotí": (-26.8000, -54.1000),
    "Parque Provincial Urugua-í": (-25.9000, -54.1500),
}

# ------------------------------------------------------------------ UTILIDADES
sesion = requests.Session()
sesion.headers.update({"User-Agent": USER_AGENT})
_robots_cache = {}
_fuentes_cache = {}
ESTADISTICAS = {
    "descargas_ok": 0, "descargas_error": 0, "sin_texto": 0, "sin_fauna": 0,
    "bd_guardados": 0, "bd_duplicados": 0, "bd_sin_lugar": 0,
    "bd_sin_especie": 0, "bd_especie_no_cargada": 0, "bd_sin_fecha": 0, "bd_error": 0,
}
DIAGNOSTICO = True
ESPECIES_NO_CARGADAS = set()


def normalizar(texto: str) -> str:
    """Minúsculas y sin tildes, para comparar de forma robusta."""
    texto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def permitido_por_robots(url: str) -> bool:
    """Respeta el robots.txt del sitio (descargado con nuestro User-Agent)."""
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots_cache:
        rp = None
        try:
            r = sesion.get(base + "/robots.txt", timeout=15)
            if r.status_code == 200:
                rp = RobotFileParser()
                rp.parse(r.text.splitlines())
        except requests.RequestException:
            rp = None
        _robots_cache[base] = rp
    rp = _robots_cache[base]
    return True if rp is None else rp.can_fetch(USER_AGENT, url)


def descargar(url: str):
    """Devuelve el HTML o None si falla / no está permitido."""
    if not permitido_por_robots(url):
        print(f"  [robots.txt no permite] {url}")
        return None
    try:
        r = sesion.get(url, timeout=15)
        r.raise_for_status()
        ESTADISTICAS["descargas_ok"] += 1
        return r.text
    except requests.RequestException as e:
        ESTADISTICAS["descargas_error"] += 1
        print(f"  [error] {url}: {e}")
        return None
    finally:
        time.sleep(PAUSA_SEGUNDOS)


def extraer_nota(html: str) -> dict:
    """Extrae título, fecha y texto de una nota."""
    soup = BeautifulSoup(html, "html.parser")

    def meta(*nombres):
        for n in nombres:
            tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
            if tag and tag.get("content"):
                return tag["content"].strip()
        return ""

    titulo = meta("og:title") or (soup.title.string.strip() if soup.title and soup.title.string else "")
    fecha = meta("article:published_time", "date", "pubdate")

    contenedor = soup.find("article") or soup
    for basura in contenedor(["script", "style", "nav", "footer", "aside", "form"]):
        basura.decompose()
    parrafos = [p.get_text(" ", strip=True) for p in contenedor.find_all("p")]
    texto = "\n".join(p for p in parrafos if len(p) > 40)
    return {"titulo": titulo, "fecha": fecha, "texto": texto}


def analizar(texto: str):
    """Detecta especies y lugares mencionados."""
    t = normalizar(texto)
    especies = {}
    for comun, cientifico in ESPECIES.items():
        if re.search(rf"\b{re.escape(normalizar(comun))}\b", t):
            especies[comun] = cientifico or ""
    lugares = [l for l in LUGARES if normalizar(l) in t]
    clave = any(normalizar(p) in t for p in PALABRAS_CLAVE)
    return especies, lugares, clave


def urls_de_feed(nombre: str, url_feed: str):
    """Lee un feed RSS y sus páginas siguientes (?paged=N). Corta cuando no hay más."""
    urls = []
    for pagina in range(1, PAGINAS_POR_FEED + 1):
        sep = "&" if "?" in url_feed else "?"
        url = url_feed if pagina == 1 else f"{url_feed}{sep}paged={pagina}"
        print(f"Feed {nombre} (pág. {pagina})")
        html = descargar(url)
        if not html:
            break
        entradas = feedparser.parse(html).entries
        if not entradas:
            break
        urls += [(nombre, e.link) for e in entradas if "link" in e]
    return urls


def urls_de_busqueda(nombre: str, base: str, termino: str):
    """Busca un término en un WordPress. Primero API wp-json; si falla, RSS de búsqueda."""
    urls = []
    for pagina in range(1, PAGINAS_POR_BUSQUEDA + 1):
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


def cargar_set(ruta: Path, columna_csv: str = None) -> set:
    if not ruta.exists():
        return set()
    with ruta.open(encoding="utf-8") as f:
        if columna_csv:
            return {fila[columna_csv] for fila in csv.DictReader(f)}
        return {l.strip() for l in f if l.strip()}


# ------------------------------------------------------------------ BASE DE DATOS
def parsear_fecha(texto: str):
    """Convierte '2024-05-03T10:15:00-03:00' (u otro ISO) en date. Devuelve None si no se puede."""
    if not texto:
        return None
    texto = texto.strip()
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", texto)
    if m:
        try:
            return date(*map(int, m.groups()))
        except ValueError:
            return None
    return None


def elegir_lugar(lugares: list):
    """Entre los lugares detectados, el más específico (el nombre más largo)."""
    candidatos = [l for l in lugares if l in COORDENADAS]
    return max(candidatos, key=len) if candidatos else None


def obtener_fuente(url: str):
    """Fuente = el sitio de la nota (ej. 'Prensa: misionesonline.net'). Se crea si no existe."""
    dominio = urlparse(url).netloc.replace("www.", "")
    nombre = f"Prensa: {dominio}"
    if nombre not in _fuentes_cache:
        _fuentes_cache[nombre], _ = Fuente.objects.get_or_create(
            nombre=nombre, defaults=FUENTE_DEFAULTS
        )
    return _fuentes_cache[nombre]


def guardar_en_bd(url: str, nota: dict, especies: dict, lugares: list) -> int:
    """Crea un Registro por cada especie (con nombre científico) detectada. Devuelve cuántos creó."""
    lugar = elegir_lugar(lugares)
    if not lugar:
        ESTADISTICAS["bd_sin_lugar"] += 1
        print("  [BD] sin lugar con coordenadas conocidas: no se guarda")
        return 0
    lat, lon = (Decimal(str(v)) for v in COORDENADAS[lugar])

    fecha = parsear_fecha(nota["fecha"])
    if fecha is None:
        if FECHA_SI_FALTA == "hoy":
            fecha = date.today()
            ESTADISTICAS["bd_sin_fecha"] += 1
        else:
            print("  [BD] sin fecha de publicación: no se guarda")
            return 0
    if fecha > date.today():  # Registro.clean() no admite fechas futuras
        fecha = date.today()

    cientificos = sorted({c for c in especies.values() if c})
    if not cientificos:
        if REQUIERE_ESPECIE_PARA_BD:
            ESTADISTICAS["bd_sin_especie"] += 1
            print("  [BD] sin especie con nombre científico: no se guarda")
            return 0
        cientificos = [None]

    fuente = obtener_fuente(url)
    creados = 0
    for cientifico in cientificos:
        especie = None
        if cientifico:
            especie = Especie.objects.filter(nombre_cientifico__iexact=cientifico).first()
            if especie is None:
                ESTADISTICAS["bd_especie_no_cargada"] += 1
                ESPECIES_NO_CARGADAS.add(cientifico)
                print(f"  [BD] '{cientifico}' no existe en la tabla especie: no se guarda")
                continue

        # Evita duplicar si se reprocesa la misma nota/combinación
        if Registro.objects.filter(
            especie=especie, fuente=fuente, fecha_avistamiento=fecha,
            latitud=lat, longitud=lon,
        ).exists():
            ESTADISTICAS["bd_duplicados"] += 1
            continue

        try:
            with transaction.atomic():
                registro = Registro(
                    especie=especie,
                    fuente=fuente,
                    usuario_curador=None,
                    fecha_avistamiento=fecha,
                    latitud=lat,
                    longitud=lon,
                    zona=lugar,
                    estado_calidad=Registro.EstadoCalidad.PENDIENTE,
                )
                registro.full_clean()
                registro.save()
            creados += 1
            ESTADISTICAS["bd_guardados"] += 1
        except (ValidationError, Exception) as e:  # no cortar toda la corrida por un registro
            ESTADISTICAS["bd_error"] += 1
            print(f"  [BD error] {e}")
    return creados


# ------------------------------------------------------------------ MAIN
def main():
    candidatas = []
    for nombre, url_feed in FEEDS.items():
        candidatas += urls_de_feed(nombre, url_feed)
    for nombre, base in SITIOS_WP.items():
        for termino in TERMINOS_BUSQUEDA:
            candidatas += urls_de_busqueda(nombre, base, termino)
    candidatas += [("Manual", u) for u in URLS_MANUALES]

    ya = cargar_set(PROCESADAS) | cargar_set(SALIDA, "url")
    unicas, vistas_ahora = [], set()
    for fuente, url in candidatas:
        if url not in ya and url not in vistas_ahora:
            vistas_ahora.add(url)
            unicas.append((fuente, url))
    print(f"\nURLs encontradas: {len(candidatas)} | nuevas y únicas: {len(unicas)}")
    unicas = unicas[:MAX_NOTAS_POR_CORRIDA]

    nuevo_archivo = not SALIDA.exists()
    columnas = ["url", "fuente", "titulo", "fecha_publicacion", "especies",
                "nombres_cientificos", "lugares", "extracto", "fecha_scraping"]
    guardadas = 0

    with SALIDA.open("a", newline="", encoding="utf-8") as f, \
         PROCESADAS.open("a", encoding="utf-8") as reg:
        w = csv.DictWriter(f, fieldnames=columnas)
        if nuevo_archivo:
            w.writeheader()

        for i, (fuente, url) in enumerate(unicas, 1):
            print(f"[{i}/{len(unicas)}] {url}")
            html = descargar(url)
            if not html:
                continue
            reg.write(url + "\n"); reg.flush()
            nota = extraer_nota(html)
            if not nota["texto"]:
                ESTADISTICAS["sin_texto"] += 1
                if DIAGNOSTICO:
                    print("  [descartada] no se pudo extraer texto (¿la página usa otra estructura o bloquea?)")
                continue
            especies, lugares, clave = analizar(nota["titulo"] + "\n" + nota["texto"])

            if not (clave and (especies or lugares)):
                ESTADISTICAS["sin_fauna"] += 1
                if DIAGNOSTICO:
                    print(f"  [descartada] sin especie/lugar conocido: {nota['titulo'][:70]}")
                continue

            w.writerow({
                "url": url,
                "fuente": fuente,
                "titulo": nota["titulo"],
                "fecha_publicacion": nota["fecha"],
                "especies": "; ".join(especies),
                "nombres_cientificos": "; ".join(sorted({c for c in especies.values() if c})),
                "lugares": "; ".join(lugares),
                "extracto": nota["texto"][:300],
                "fecha_scraping": datetime.now().isoformat(timespec="seconds"),
            })
            f.flush()
            guardadas += 1
            print(f"  -> guardada ({', '.join(especies) or 'sin especie'} | {', '.join(lugares) or 'sin lugar'})")

            if GUARDAR_EN_BD:
                n = guardar_en_bd(url, nota, especies, lugares)
                if n:
                    print(f"  -> {n} avistamiento(s) creado(s) en la base de datos")

    print(f"\nResumen: {ESTADISTICAS}")
    print(f"Listo. Notas nuevas guardadas en CSV: {guardadas}. Archivo: {SALIDA.resolve()}")
    if ESPECIES_NO_CARGADAS:
        print("Especies detectadas que faltan en la tabla `especie`: "
              + ", ".join(sorted(ESPECIES_NO_CARGADAS)))
    if len(candidatas) and len(unicas) == MAX_NOTAS_POR_CORRIDA:
        print("Se alcanzó el tope por corrida: volvé a ejecutar para seguir con las que faltan.")


if __name__ == "__main__":
    main()