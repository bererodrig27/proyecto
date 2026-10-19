"""Texto de lugar -> (latitud, longitud). Diccionario local, caché y Nominatim como respaldo."""
import json
import re
import time

import requests

from . import config
from .normalizar import normalizar_texto, en_misiones

_cache = None
_ultimo_pedido = 0.0
_COORD_NORM = {normalizar_texto(k): v for k, v in config.COORDENADAS.items()}


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
    """El lugar más específico (nombre más largo) del diccionario contenido en el texto."""
    hallados = [k for k in _COORD_NORM if re.search(rf"\b{re.escape(k)}\b", clave)]
    return _COORD_NORM[max(hallados, key=len)] if hallados else None


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
                "countrycodes": "ar", "bounded": 1,
                "viewbox": f"{lon_min},{lat_max},{lon_max},{lat_min}",
            },
            headers={"User-Agent": config.USER_AGENT}, timeout=config.TIMEOUT,
        )
        _ultimo_pedido = time.time()
        r.raise_for_status()
        datos = r.json()
        return (float(datos[0]["lat"]), float(datos[0]["lon"])) if datos else None
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return None


def geocodificar(lugar_texto: str):
    """Devuelve (lat, lon, metodo) o None. Solo acepta puntos dentro de Misiones."""
    clave = normalizar_texto(lugar_texto)
    if not clave:
        return None

    punto = _desde_diccionario(clave)
    if punto:
        return (*punto, "diccionario")

    cache = _cargar_cache()
    if clave in cache:
        v = cache[clave]
        return (v[0], v[1], "cache") if v else None

    if not config.USAR_NOMINATIM:
        return None
    punto = _desde_nominatim(lugar_texto)
    if punto and not en_misiones(*punto):
        punto = None
    cache[clave] = list(punto) if punto else None   # también guarda los fallos
    _guardar_cache()
    return (*punto, "nominatim") if punto else None