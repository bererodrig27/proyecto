"""Texto de lugar -> (latitud, longitud). Diccionario local, caché y Nominatim como respaldo."""
import json
import re
import time
from collections import namedtuple

import requests

from . import config
from .normalizar import normalizar_texto, en_misiones

# metodo: diccionario | cache | nominatim. zona: nombre del área o departamento ('' si no se sabe).
Punto = namedtuple("Punto", "lat lon metodo zona")

_cache = None
_ultimo_pedido = 0.0
_COORD_NORM = {normalizar_texto(k): v for k, v in config.COORDENADAS.items()}
_NOMBRE_NORM = {normalizar_texto(k): k for k in config.COORDENADAS}


def _cargar_cache() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(config.GEOCACHE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _guardar_cache():
    config.GEOCACHE.write_text(json.dumps(_cache, ensure_ascii=False, indent=1), encoding="utf-8")


def _desde_diccionario(clave: str):
    """El lugar más específico (nombre más largo) del diccionario contenido en el texto.
    Devuelve ((lat, lon), nombre_del_lugar) o None."""
    hallados = [k for k in _COORD_NORM if re.search(rf"\b{re.escape(k)}\b", clave)]
    if not hallados:
        return None
    mejor = max(hallados, key=len)
    return _COORD_NORM[mejor], _NOMBRE_NORM[mejor]


def _desde_nominatim(lugar: str):
    global _ultimo_pedido
    espera = config.NOMINATIM_PAUSA - (time.time() - _ultimo_pedido)
    if espera > 0:
        time.sleep(espera)
    lat_min, lat_max, lon_min, lon_max = config.MISIONES_BBOX
    try:
        r = requests.get(
            config.NOMINATIM_URL,
            params={
                "q": f"{lugar}, Misiones, Argentina", "format": "json", "limit": 1,
                "countrycodes": "ar", "bounded": 1, "addressdetails": 1,
                "viewbox": f"{lon_min},{lat_max},{lon_max},{lat_min}",
            },
            headers={"User-Agent": config.USER_AGENT}, timeout=config.TIMEOUT,
        )
        _ultimo_pedido = time.time()
        r.raise_for_status()
        datos = r.json()
        if not datos:
            return None
        dir_ = datos[0].get("address") or {}
        zona = dir_.get("county") or dir_.get("city") or dir_.get("town") or dir_.get("village") or ""
        return float(datos[0]["lat"]), float(datos[0]["lon"]), zona
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return None


def geocodificar(lugar_texto: str):
    """Devuelve un Punto(lat, lon, metodo, zona) o None. Solo acepta puntos dentro de Misiones."""
    clave = normalizar_texto(lugar_texto)
    if not clave:
        return None

    hallado = _desde_diccionario(clave)
    if hallado:
        (lat, lon), nombre = hallado
        return Punto(lat, lon, "diccionario", nombre)

    cache = _cargar_cache()
    if clave in cache:
        v = cache[clave]
        # las entradas viejas de la caché tienen solo [lat, lon]; las nuevas, [lat, lon, zona]
        return Punto(v[0], v[1], "cache", v[2] if len(v) > 2 else "") if v else None

    if not config.USAR_NOMINATIM:
        return None
    resultado = _desde_nominatim(lugar_texto)
    if resultado and not en_misiones(resultado[0], resultado[1]):
        resultado = None
    cache[clave] = list(resultado) if resultado else None   # también guarda los fallos
    _guardar_cache()
    return Punto(resultado[0], resultado[1], "nominatim", resultado[2]) if resultado else None
