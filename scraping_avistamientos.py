import argparse
import json
import os
import re
import sys

import django
import requests
from bs4 import BeautifulSoup


# ============================================================
# CONFIGURACIÓN DE DJANGO
# ============================================================

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tu_proyecto.settings")
django.setup()

from tu_app.models import Especie


# ============================================================
# CONFIGURACIÓN
# ============================================================

URL_DEFECTO = "https://misionesonline.net/2026/08/06/avistamiento-yaguarete-corrientes/"

HEADERS = {
    "User-Agent": (
        "BuscadorBiodiversidadMisiones/1.0 "
        "(proyecto educativo)"
    )
}


# ============================================================
# LOCALIDADES
# ============================================================

LOCALIDADES = {
    "mburucuya": ("Corrientes", -28.05, -58.23),
    "santa rosa": ("Corrientes", -28.26, -58.12),
    "saladas": ("Corrientes", -28.25, -58.62),
    "posadas": ("Misiones", -27.37, -55.90),
    "puerto iguazu": ("Misiones", -25.60, -54.57),
    "iguazu": ("Misiones", -25.60, -54.57),
    "eldorado": ("Misiones", -26.40, -54.62),
    "obera": ("Misiones", -27.49, -55.12),
    "san ignacio": ("Misiones", -27.25, -55.53),
}


PROVINCIAS = {
    "misiones": (-27.36, -55.90),
    "corrientes": (-27.47, -58.83),
}


# ============================================================
# DESCARGAR PÁGINA
# ============================================================

def descargar(url):
    try:
        respuesta = requests.get(
            url,
            headers=HEADERS,
            timeout=20
        )

        if respuesta.status_code in (403, 429, 503):
            print(
                f"El sitio respondió {respuesta.status_code}. "
                "Puede existir protección anti-bot.",
                file=sys.stderr
            )
            return None

        respuesta.raise_for_status()
        return respuesta.text

    except requests.RequestException as e:
        print(f"Error al descargar la página: {e}", file=sys.stderr)
        return None


# ============================================================
# LIMPIAR TEXTO
# ============================================================

def limpio(texto):
    return re.sub(r"\s+", " ", texto).strip()


# ============================================================
# CARGAR ESPECIES DESDE LA BASE DE DATOS
# ============================================================

def cargar_especies():
    return list(
        Especie.objects.values(
            "id_especie",
            "nombre_comun",
            "nombre_cientifico"
        )
    )


# ============================================================
# DETECTAR ESPECIE
# ============================================================

def detectar_especie(texto, titulo, especies):
    texto = texto.lower()
    titulo = titulo.lower()

    encontradas = []

    for especie in especies:
        nombre = especie["nombre_comun"]

        if not nombre:
            continue

        nombre = nombre.lower()

        # Primero damos prioridad a las especies
        # mencionadas en el título.
        if nombre in titulo:
            encontradas.append((especie, 2))
            continue

        if nombre in texto:
            encontradas.append((especie, 1))

    if not encontradas:
        return None

    # Prioridad:
    # 1. aparece en título
    # 2. nombre más largo
    encontradas.sort(
        key=lambda x: (
            x[1],
            len(x[0]["nombre_comun"])
        ),
        reverse=True
    )

    return encontradas[0][0]


# ============================================================
# EXTRAER NOMBRE CIENTÍFICO
# ============================================================

def detectar_nombre_cientifico(texto, especie):
    if not especie:
        return None

    nombre = especie["nombre_cientifico"]

    if not nombre:
        return None

    if nombre.lower() in texto.lower():
        return nombre

    return None


# ============================================================
# EXTRAER UBICACIÓN
# ============================================================

def extraer_ubicacion(texto):
    texto_lower = texto.lower()

    # Coordenadas explícitas
    patron = r"(-?\d{1,3}\.\d+)\s*[,;]\s*(-?\d{1,3}\.\d+)"

    coordenadas = re.search(patron, texto)

    if coordenadas:
        return {
            "provincia": None,
            "localidad": None,
            "latitud": float(coordenadas.group(1)),
            "longitud": float(coordenadas.group(2))
        }

    # Buscar localidades conocidas
    for localidad, datos in LOCALIDADES.items():

        if localidad in texto_lower:
            provincia, latitud, longitud = datos

            return {
                "provincia": provincia,
                "localidad": localidad.title(),
                "latitud": latitud,
                "longitud": longitud
            }

    # Buscar provincias
    for provincia, coordenadas in PROVINCIAS.items():

        if provincia in texto_lower:
            latitud, longitud = coordenadas

            return {
                "provincia": provincia.title(),
                "localidad": None,
                "latitud": latitud,
                "longitud": longitud
            }

    return {
        "provincia": None,
        "localidad": None,
        "latitud": None,
        "longitud": None
    }


# ============================================================
# EXTRAER INFORMACIÓN DE LA NOTICIA
# ============================================================

def extraer(html, url, especies):

    soup = BeautifulSoup(html, "html.parser")

    # Título
    titulo_tag = soup.find("h1")

    titulo = limpio(
        titulo_tag.get_text(" ", strip=True)
        if titulo_tag
        else ""
    )

    # Cuerpo de la noticia
    parrafos = []

    for p in soup.find_all("p"):
        texto = limpio(p.get_text(" ", strip=True))

        if texto:
            parrafos.append(texto)

    cuerpo = " ".join(parrafos)

    texto_completo = f"{titulo} {cuerpo}"

    # Especie
    especie = detectar_especie(
        texto_completo,
        titulo,
        especies
    )

    # Nombre científico
    nombre_cientifico = detectar_nombre_cientifico(
        texto_completo,
        especie
    )

    # Ubicación
    ubicacion = extraer_ubicacion(texto_completo)

    return {
        "id_especie": (
            especie["id_especie"]
            if especie
            else None
        ),

        "nombre_comun": (
            especie["nombre_comun"]
            if especie
            else None
        ),

        "nombre_cientifico": nombre_cientifico,

        "ubicacion": ubicacion,

        "url": url
    }


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description="Scraper de avistamientos de biodiversidad"
    )

    parser.add_argument(
        "--url",
        default=URL_DEFECTO,
        help="URL de la noticia"
    )

    parser.add_argument(
        "--html",
        help="Archivo HTML guardado localmente"
    )

    args = parser.parse_args()

    # Cargar especies desde Oracle
    especies = cargar_especies()

    if not especies:
        print(
            "No hay especies cargadas en la tabla Especie.",
            file=sys.stderr
        )
        sys.exit(1)

    # Obtener HTML
    if args.html:

        try:
            with open(
                args.html,
                "r",
                encoding="utf-8"
            ) as archivo:
                html = archivo.read()

        except OSError as e:
            print(
                f"Error al leer el HTML: {e}",
                file=sys.stderr
            )
            sys.exit(1)

    else:
        html = descargar(args.url)

        if not html:
            sys.exit(1)

    # Extraer información
    resultado = extraer(
        html,
        args.url,
        especies
    )

    # Mostrar JSON
    print(
        json.dumps(
            resultado,
            ensure_ascii=False,
            indent=2
        )
    )


if __name__ == "__main__":
    main()