"""Persistencia: Fuente -> Noticia -> Ubicacion -> Registro (PENDIENTE) -> ExtraccionIA,
más el CSV de auditoría. Requiere que Django ya esté inicializado (lo hace __main__.py).

Cada avistamiento queda PENDIENTE: lo revisa después el Administrador (aceptar, corregir
o rechazar). La noticia de origen se conserva con su URL para que la procedencia sea
verificable.
"""
import csv
import re
from datetime import datetime
from decimal import Decimal
from importlib import import_module
from urllib.parse import urlparse

from django.db import transaction

from . import config
from .descubrir import cargar_procesadas
from .normalizar import parsear_fecha, normalizar_texto

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
_catalogo = None


def _cargar_catalogo() -> dict:
    """Catálogo de especies en memoria, con los nombres normalizados (sin tildes ni mayúsculas).
    Se lee una sola vez por corrida. nombre_comun puede traer varios nombres separados por , ; / o ' o '."""
    global _catalogo
    if _catalogo is None:
        _catalogo = {"cientifico": {}, "comun": {}}
        for e in Especie.objects.all():
            _catalogo["cientifico"][normalizar_texto(e.nombre_cientifico)] = e
            for alias in re.split(r"\s*[,;/]\s*|\s+o\s+", e.nombre_comun or ""):
                alias = normalizar_texto(alias)
                if alias:
                    _catalogo["comun"].setdefault(alias, []).append(e)
    return _catalogo


def buscar_especie(candidatos: list, nombre_comun: str):
    """Especie del catálogo (tabla especie) o None si no está cargada.

    1) por nombre científico; 2) por nombre común exacto; 3) por nombre común contenido en
    el que dio la IA ('yaguareté overo' -> 'Yaguareté'). En 2 y 3 solo si es una única especie.
    La comparación ignora tildes y mayúsculas.
    """
    cat = _cargar_catalogo()
    for cientifico in candidatos:
        especie = cat["cientifico"].get(normalizar_texto(cientifico))
        if especie:
            return especie
    comun = normalizar_texto(nombre_comun)
    if not comun:
        return None
    exactas = {e.pk: e for e in cat["comun"].get(comun, [])}
    if len(exactas) == 1:
        return next(iter(exactas.values()))
    contenidas = {e.pk: e for alias, lista in cat["comun"].items()
                  if re.search(rf"\b{re.escape(alias)}\b", comun) for e in lista}
    return next(iter(contenidas.values())) if len(contenidas) == 1 else None


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


def _obtener_ubicacion(geo):
    """Ubicacion del avistamiento. En vez del fragmento de texto de la nota se guarda el
    lugar normalizado (localidad o área) y el departamento; el texto original queda en
    extraccion_ia.json_extraido y en el CSV de auditoría."""
    lat, lon = round(geo.lat, config.COORD_DECIMALES), round(geo.lon, config.COORD_DECIMALES)
    nombre = (geo.zona or geo.departamento or "Misiones")[:200]
    existente = Ubicacion.objects.filter(
        nombre_lugar=nombre, departamento=geo.departamento,
        latitud__range=(lat - _TOLERANCIA, lat + _TOLERANCIA),
        longitud__range=(lon - _TOLERANCIA, lon + _TOLERANCIA),
    ).first()
    if existente:
        return existente
    ubicacion = Ubicacion(
        nombre_lugar=nombre, departamento=geo.departamento,
        zona=(geo.zona or "")[:100],   # queda como antes; ya no hace falta si usás departamento
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
        ubicacion=_obtener_ubicacion(geo) if geo else None,
        fecha_avistamiento=av["fecha"],  # None solo con FECHA_SI_FALTA = "nula" / "publicacion"
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
             "departamento", "metodo_geo", "fecha", "confianza", "cita", "resultado", "fecha_scraping"]


def registrar_csv(fila: dict):
    nuevo = not config.LOG_CSV.exists()
    with config.LOG_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_COLUMNAS, extrasaction="ignore")
        if nuevo:
            w.writeheader()
        w.writerow({**fila, "fecha_scraping": datetime.now().isoformat(timespec="seconds")})
