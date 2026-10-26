"""Confianza de una extracción, calculada por el sistema con controles de calidad.

No se usa la confianza que declara el modelo de lenguaje: se verifica lo que devolvió
contra el texto de la nota, el catálogo de especies y la geocodificación. El resultado
(entre 0 y 1) se guarda en extraccion_ia.confianza y el desglose, en json_extraido.
"""
import re

from . import config
from .normalizar import normalizar_texto

PUNTAJE_GEO = {"diccionario": 1.0, "cache": 0.6, "nominatim": 0.6}
PUNTAJE_FECHA = {"ia": 1.0, "publicacion": 0.4, "hoy": 0.2}


def _palabras(texto: str) -> list:
    return re.findall(r"\w+", normalizar_texto(texto))


def _presencia(valor: str, texto_norm: str) -> float:
    """1.0 si la frase aparece en el texto; 0.5 si aparece casi todo su contenido; 0 si no.

    El texto llega normalizado (minúsculas, sin tildes, palabras separadas por un espacio).
    Se compara solo al inicio de cada palabra para tolerar plurales ('tucán' ~ 'tucanes').
    """
    palabras = _palabras(valor)
    if not palabras:
        return 0.0
    if " " + " ".join(palabras) in " " + texto_norm:
        return 1.0
    significativas = [p for p in palabras if len(p) >= 4]   # sin 'de', 'del', 'los', números cortos
    if not significativas:
        return 0.0
    en_texto = set(texto_norm.split())
    halladas = sum(1 for p in significativas if any(t.startswith(p) for t in en_texto))
    return 0.5 if halladas / len(significativas) >= 0.8 else 0.0


def calcular_confianza(av: dict, texto: str, en_catalogo: bool, geo):
    """Devuelve (confianza 0-1 redondeada a 4 decimales, desglose por control)."""
    texto_norm = " ".join(_palabras(texto))

    if en_catalogo:
        especie_catalogo = 1.0
    else:   # al menos vino un nombre científico con formato binomial, aunque no esté cargado
        especie_catalogo = 0.3 if av["candidatos_cientificos"] else 0.0

    controles = {
        "cita_en_texto": _presencia(av["cita"], texto_norm),
        "especie_en_catalogo": especie_catalogo,
        "especie_en_texto": _presencia(av["nombre_comun"], texto_norm),
        "lugar_en_texto": _presencia(av["lugar_texto"], texto_norm) if av["lugar_texto"] else 0.0,
        "fecha": PUNTAJE_FECHA.get(av["fecha_origen"], 0.0),
        "geocodificacion": PUNTAJE_GEO.get(geo.metodo, 0.0) if geo else 0.0,
    }

    pesos = config.PESOS_CONFIANZA
    total = sum(pesos.values())
    confianza = sum(pesos.get(nombre, 0) * valor for nombre, valor in controles.items()) / total
    return round(max(0.0, min(1.0, confianza)), 4), controles
