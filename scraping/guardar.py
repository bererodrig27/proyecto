"""Persistencia: Fuente -> Noticia -> Ubicacion -> Registro (PENDIENTE) -> ExtraccionIA,
más el CSV de auditoría. Requiere que Django ya esté inicializado (lo hace __main__.py).

Cada avistamiento queda PENDIENTE: lo revisa después el Administrador (aceptar, corregir
o rechazar). La noticia de origen se conserva con su URL para que la procedencia sea
verificable.
"""
import csv
from datetime import datetime
from decimal import Decimal
from importlib import import_module
from urllib.parse import urlparse

from django.db import transaction

from . import config
from .descubrir import cargar_procesadas
from .normalizar import parsear_fecha

_models = import_module(f"{config.APP_MODELOS}.models")
Especie, Fuente, Noticia = _models.Especie, _models.Fuente, _models.Noticia
Registro, Ubicacion, ExtraccionIA = _models.Registro, _models.Ubicacion, _models.ExtraccionIA

Estado = Noticia.EstadoProcesamiento

_fuentes_cache = {}
ESPECIES_NO_CARGADAS = set()

# Dos coordenadas redondeadas a COORD_DECIMALES se consideran el mismo punto si difieren
# menos de media unidad del último decimal.
_TOLERANCIA = 0.5 * 10 ** -config.COORD_DECIMALES


# ------------------------------------------------------------------ noticias y fuentes
def urls_ya_procesadas() -> set:
    """URLs que no hace falta volver a descargar.

    Con base de datos se leen de la tabla noticia (la URL es única). PENDIENTE y ERROR
    no cuentan: quedaron a medias y se reintentan en la próxima corrida.
    """
    if not config.GUARDAR_EN_BD:
        return cargar_procesadas()
    return set(
        Noticia.objects
        .filter(estado_procesamiento__in=[Estado.PROCESADA, Estado.DESCARTADA])
        .values_list("url", flat=True)
    )


def obtener_fuente(url: str):
    """Fuente = medio de comunicación. Se identifica por el dominio de la nota."""
    p = urlparse(url)
    dominio = p.netloc.replace("www.", "")
    nombre, base = dominio, f"{p.scheme}://{p.netloc}"
    for nombre_medio, url_medio in config.SITIOS_WP.items():
        if urlparse(url_medio).netloc.replace("www.", "") == dominio:
            nombre, base = nombre_medio, url_medio
            break
    if nombre not in _fuentes_cache:
        _fuentes_cache[nombre], _ = Fuente.objects.get_or_create(
            nombre=nombre, defaults={"url_base": base},
        )
    return _fuentes_cache[nombre]


def registrar_noticia(url: str, fuente, nota: dict, relevante: bool):
    """Crea la Noticia, o la actualiza si quedó PENDIENTE/ERROR en una corrida anterior.

    Solo se guarda un extracto (config.EXTRACTO_CHARS), y únicamente de las notas que
    llegan a la IA; de las descartadas queda la URL y el título.
    """
    datos = {
        "fuente": fuente,
        "titulo": " ".join((nota["titulo"] or url).split())[:300],
        "fecha_publicacion": parsear_fecha(nota["fecha"]),
        "extracto": nota["texto"][: config.EXTRACTO_CHARS] if relevante else "",
        "origen": Noticia.Origen.SCRAPING,
        "estado_procesamiento": Estado.PENDIENTE if relevante else Estado.DESCARTADA,
    }
    noticia, _ = Noticia.objects.update_or_create(url=url, defaults=datos)
    return noticia


def cerrar_noticia(noticia, estado):
    noticia.estado_procesamiento = estado
    noticia.save(update_fields=["estado_procesamiento"])


# ------------------------------------------------------------------ avistamientos
def buscar_especie(candidatos: list, nombre_comun: str):
    """Especie del catálogo por nombre científico; si no, por nombre común (solo si es único)."""
    for cientifico in candidatos:
        especie = Especie.objects.filter(nombre_cientifico__iexact=cientifico).first()
        if especie:
            return especie
    if nombre_comun:
        coincidencias = list(Especie.objects.filter(nombre_comun__iexact=nombre_comun)[:2])
        if len(coincidencias) == 1:
            return coincidencias[0]
    return None


def es_duplicado(especie, fecha, geo) -> bool:
    """Misma especie, fecha y punto que un registro existente (p. ej. dos medios cubren el
    mismo hecho). Sin especie, fecha o punto no se puede afirmar que sea el mismo hecho."""
    if especie is None or fecha is None or geo is None:
        return False
    lat, lon = round(geo.lat, config.COORD_DECIMALES), round(geo.lon, config.COORD_DECIMALES)
    return Registro.objects.filter(
        especie=especie, fecha_avistamiento=fecha,
        ubicacion__latitud__range=(lat - _TOLERANCIA, lat + _TOLERANCIA),
        ubicacion__longitud__range=(lon - _TOLERANCIA, lon + _TOLERANCIA),
    ).exists()


def _obtener_ubicacion(av: dict, geo):
    lat, lon = round(geo.lat, config.COORD_DECIMALES), round(geo.lon, config.COORD_DECIMALES)
    existente = Ubicacion.objects.filter(
        nombre_lugar=av["lugar_texto"],
        latitud__range=(lat - _TOLERANCIA, lat + _TOLERANCIA),
        longitud__range=(lon - _TOLERANCIA, lon + _TOLERANCIA),
    ).first()
    if existente:
        return existente
    ubicacion = Ubicacion(
        nombre_lugar=av["lugar_texto"], zona=(geo.zona or "")[:100],
        latitud=lat, longitud=lon, precision=config.PRECISION_UBICACION,
    )
    ubicacion.full_clean()
    ubicacion.save()
    return ubicacion


@transaction.atomic
def guardar_avistamiento(noticia, av: dict, especie, geo, confianza: float, json_extraido: dict):
    """Crea Ubicacion (si hay punto), Registro PENDIENTE y su ExtraccionIA: todo o nada."""
    registro = Registro(
        noticia=noticia,
        especie=especie,                 # None hasta que el Administrador la resuelva
        ubicacion=_obtener_ubicacion(av, geo) if geo else None,
        fecha_avistamiento=av["fecha"],  # None si la nota no la precisa
        tipo_registro=av["tipo_registro"],
        estado_calidad=Registro.EstadoCalidad.PENDIENTE,
        fragmento_origen=av["cita"],
        # usuario_carga y usuario_validador quedan en None: lo carga el scraping
    )
    registro.full_clean()
    registro.save()

    extraccion = ExtraccionIA(
        registro=registro,
        modelo=config.GEMINI_MODEL[:100],
        version_prompt=config.PROMPT_VERSION,
        json_extraido=json_extraido,
        confianza=Decimal(f"{confianza:.4f}"),
    )
    extraccion.full_clean()
    extraccion.save()
    return registro


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
