"""Texto de lugar -> (latitud, longitud, departamento). Diccionario local, caché y Nominatim como respaldo."""
import json
import re
import time
from collections import namedtuple

import requests

from . import config
from .normalizar import normalizar_texto, normalizar_departamento, en_misiones

# metodo: diccionario | cache | nominatim.
# zona: nombre de la localidad o área ('' si no se sabe). departamento: uno de config.DEPARTAMENTOS ('' si no se sabe).
Punto = namedtuple("Punto", "lat lon metodo zona departamento")

_cache = None
_ultimo_pedido = 0.0
_COORD_NORM = {normalizar_texto(k): (v[0], v[1]) for k, v in config.COORDENADAS.items()}
_DEPTO_NORM = {normalizar_texto(k): v[2] for k, v in config.COORDENADAS.items()}
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


def desde_diccionario(clave: str):
    """El lugar más específico (nombre más largo) del diccionario contenido en el texto.
    `clave` ya viene normalizado. Devuelve ((lat, lon), nombre_del_lugar, departamento) o None."""
    hallados = [k for k in _COORD_NORM if re.search(rf"\b{re.escape(k)}\b", clave)]
    if not hallados:
        return None
    mejor = max(hallados, key=len)
    return _COORD_NORM[mejor], _NOMBRE_NORM[mejor], _DEPTO_NORM[mejor]


def _desde_nominatim(lugar: str):
    """Devuelve (lat, lon, zona, departamento) o None."""
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
        # En Misiones, OpenStreetMap suele informar el departamento como 'county'.
        departamento = (normalizar_departamento(dir_.get("county"))
                        or normalizar_departamento(dir_.get("state_district")))
        zona = (dir_.get("city") or dir_.get("town") or dir_.get("village")
                or dir_.get("hamlet") or dir_.get("county") or "")
        return float(datos[0]["lat"]), float(datos[0]["lon"]), zona, departamento
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return None


def geocodificar(lugar_texto: str):
    """Devuelve un Punto(lat, lon, metodo, zona, departamento) o None. Solo acepta puntos dentro de Misiones."""
    clave = normalizar_texto(lugar_texto)
    if not clave:
        return None

    hallado = desde_diccionario(clave)
    if hallado:
        (lat, lon), nombre, departamento = hallado
        return Punto(lat, lon, "diccionario", nombre, departamento)

    cache = _cargar_cache()
    if clave in cache:
        v = cache[clave]
        if not v:
            return None
        # Formatos de la caché: [lat, lon] (viejo), [lat, lon, zona] y [lat, lon, zona, departamento].
        zona = v[2] if len(v) > 2 else ""
        departamento = v[3] if len(v) > 3 else normalizar_departamento(zona)
        return Punto(v[0], v[1], "cache", zona, departamento)

    if not config.USAR_NOMINATIM:
        return None
    resultado = _desde_nominatim(lugar_texto)
    if resultado and not en_misiones(resultado[0], resultado[1]):
        resultado = None
    cache[clave] = list(resultado) if resultado else None   # también guarda los fallos
    _guardar_cache()
    return Punto(*resultado[:2], "nominatim", *resultado[2:]) if resultado else None
