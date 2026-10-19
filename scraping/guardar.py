"""Inserción en la BD (estado PENDIENTE) y registro CSV de auditoría.
Requiere que Django ya esté inicializado (lo hace __main__.py)."""
import csv
from datetime import datetime
from decimal import Decimal
from importlib import import_module
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.db import transaction

from . import config

_models = import_module(f"{config.APP_MODELOS}.models")
Especie, Fuente, Registro = _models.Especie, _models.Fuente, _models.Registro

_fuentes_cache = {}
ESPECIES_NO_CARGADAS = set()
_CAMPOS_REGISTRO = {f.name for f in Registro._meta.get_fields()}


def obtener_fuente(url: str):
    dominio = urlparse(url).netloc.replace("www.", "")
    nombre = f"Prensa: {dominio}"
    if nombre not in _fuentes_cache:
        _fuentes_cache[nombre], _ = Fuente.objects.get_or_create(
            nombre=nombre, defaults=config.FUENTE_DEFAULTS
        )
    return _fuentes_cache[nombre]


def _buscar_especie(candidatos: list):
    for cientifico in candidatos:
        e = Especie.objects.filter(nombre_cientifico__iexact=cientifico).first()
        if e:
            return e
    return None


def guardar_registro(av: dict, lat: float, lon: float, url: str) -> str:
    """Crea un Registro PENDIENTE. Devuelve: creado | duplicado | sin_especie |
    especie_no_cargada | error."""
    especie = _buscar_especie(av["candidatos_cientificos"])
    if especie is None:
        if av["candidatos_cientificos"]:
            ESPECIES_NO_CARGADAS.update(av["candidatos_cientificos"][:1])
            print(f"  [BD] '{av['candidatos_cientificos'][0]}' no está en `especie`: no se guarda")
            if config.REQUIERE_ESPECIE_PARA_BD:
                return "especie_no_cargada"
        elif config.REQUIERE_ESPECIE_PARA_BD:
            print(f"  [BD] '{av['nombre_comun']}' sin nombre científico: no se guarda")
            return "sin_especie"

    q = Decimal(1).scaleb(-config.COORD_DECIMALES)
    latitud, longitud = Decimal(str(lat)).quantize(q), Decimal(str(lon)).quantize(q)
    fuente = obtener_fuente(url)

    if Registro.objects.filter(
        especie=especie, fuente=fuente, fecha_avistamiento=av["fecha"],
        latitud=latitud, longitud=longitud,
    ).exists():
        return "duplicado"

    datos = {
        "especie": especie, "fuente": fuente, "usuario_curador": None,
        "fecha_avistamiento": av["fecha"], "latitud": latitud, "longitud": longitud,
        "zona": av["lugar_texto"][:200],
        "estado_calidad": Registro.EstadoCalidad.PENDIENTE,
    }
    extra = {"cita": av["cita"], "confianza": av["confianza"], "url": url}
    for clave, campo in config.CAMPOS_OPCIONALES_REGISTRO.items():
        if campo in _CAMPOS_REGISTRO:
            datos[campo] = extra[clave]

    try:
        with transaction.atomic():
            registro = Registro(**datos)
            registro.full_clean()
            registro.save()
        return "creado"
    except (ValidationError, Exception) as e:  # un registro malo no corta la corrida
        print(f"  [BD error] {e}")
        return "error"


# ------------------------------------------------------------------ CSV de auditoría
_COLUMNAS = ["url", "titulo", "nombre_comun", "nombre_cientifico", "lugar_texto", "lat", "lon",
             "metodo_geo", "fecha", "confianza", "cita", "resultado", "fecha_scraping"]


def registrar_csv(fila: dict):
    nuevo = not config.LOG_CSV.exists()
    with config.LOG_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_COLUMNAS, extrasaction="ignore")
        if nuevo:
            w.writeheader()
        w.writerow({**fila, "fecha_scraping": datetime.now().isoformat(timespec="seconds")})