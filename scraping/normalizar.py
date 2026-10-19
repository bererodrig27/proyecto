"""Normalización: texto, taxonomía, fechas y validación de que el punto cae en Misiones."""
import re
import unicodedata
from datetime import date, datetime

from . import config


def normalizar_texto(texto: str) -> str:
    """Minúsculas y sin tildes, para comparar de forma robusta."""
    texto = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn").strip()


def en_misiones(lat: float, lon: float) -> bool:
    lat_min, lat_max, lon_min, lon_max = config.MISIONES_BBOX
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def parsear_fecha(texto):
    """'2024-05-03' o ISO completo -> date. None si no se puede."""
    if not texto or not isinstance(texto, str):
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


def resolver_fecha(fecha_ia, fecha_pub):
    """Fecha del avistamiento: la de la IA si es válida; si no, según FECHA_SI_FALTA."""
    hoy = date.today()
    f = parsear_fecha(fecha_ia)
    if f and date(1900, 1, 1) <= f <= hoy:
        return f
    if config.FECHA_SI_FALTA == "publicacion":
        p = parsear_fecha(fecha_pub)
        if p:
            return min(p, hoy)
    return None


def nombres_cientificos_candidatos(nombre_comun, nombre_cientifico) -> list:
    """Lista ordenada de científicos posibles: primero el de la IA, luego el del diccionario."""
    candidatos = []
    if nombre_cientifico and isinstance(nombre_cientifico, str):
        partes = nombre_cientifico.strip().split()
        if len(partes) >= 2:  # género + epíteto, "Genus species"
            candidatos.append(f"{partes[0].capitalize()} {partes[1].lower()}")
    comun = normalizar_texto(nombre_comun)
    cientifico = config.ESPECIES.get(comun)
    if not cientifico:  # coincidencia por palabra dentro de "yaguareté overo", etc.
        for k, v in config.ESPECIES.items():
            if re.search(rf"\b{re.escape(k)}\b", comun):
                cientifico = v
                break
    if cientifico and cientifico not in candidatos:
        candidatos.append(cientifico)
    return candidatos


def normalizar_avistamiento(raw: dict, fecha_pub: str):
    """Valida y limpia un avistamiento crudo de la IA.
    Devuelve (dato, None) o (None, motivo_de_descarte)."""
    comun = (raw.get("nombre_comun") or "").strip()
    lugar = (raw.get("lugar_texto") or "").strip()
    if not comun:
        return None, "sin nombre común"
    if not lugar:
        return None, "sin lugar"

    try:
        confianza = float(raw.get("confianza"))
    except (TypeError, ValueError):
        confianza = 0.0
    confianza = max(0.0, min(1.0, confianza))
    if confianza < config.CONFIANZA_MINIMA:
        return None, f"confianza baja ({confianza})"

    fecha = resolver_fecha(raw.get("fecha_avistamiento"), fecha_pub)
    if fecha is None:
        return None, "sin fecha válida"

    return {
        "nombre_comun": comun,
        "candidatos_cientificos": nombres_cientificos_candidatos(comun, raw.get("nombre_cientifico")),
        "lugar_texto": lugar,
        "fecha": fecha,
        "cita": " ".join((raw.get("cita") or "").split()[:25]),
        "confianza": confianza,
    }, None