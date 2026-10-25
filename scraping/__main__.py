"""Orquestador. Ejecutar desde la carpeta de manage.py:  python -m scraping

Flujo por cada nota nueva:
  descubrir -> descargar -> filtro previo -> IA -> normalizar -> geocodificar ->
  buscar especie -> calcular confianza -> guardar (Noticia, Ubicacion, Registro, ExtraccionIA)
"""
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scraping import config  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", config.DJANGO_SETTINGS_MODULE)
import django  # noqa: E402
django.setup()

from scraping.confianza import calcular_confianza  # noqa: E402
from scraping.descubrir import descubrir, marcar_procesada  # noqa: E402
from scraping.descargar import descargar, extraer_nota, ESTADISTICAS as EST_DESCARGA  # noqa: E402
from scraping.extraer_ia import extraer_avistamientos, parece_relevante  # noqa: E402
from scraping.normalizar import normalizar_avistamiento  # noqa: E402
from scraping.geocodificar import geocodificar  # noqa: E402
from scraping import guardar  # noqa: E402

Estado = guardar.Estado


def main():
    stats = Counter()
    notas = descubrir(guardar.urls_ya_procesadas())

    for i, (_origen, url) in enumerate(notas, 1):
        print(f"[{i}/{len(notas)}] {url}")
        procesar_nota(url, stats)

    print(f"\nResumen: {dict(stats)} | descargas: {EST_DESCARGA}")
    if guardar.ESPECIES_NO_CARGADAS:
        print("Especies que no están en el catálogo: " + ", ".join(sorted(guardar.ESPECIES_NO_CARGADAS)))


def procesar_nota(url: str, stats: Counter):
    fuente = guardar.obtener_fuente(url) if config.GUARDAR_EN_BD else None
    if fuente is not None and not fuente.activa:
        stats["fuente_inactiva"] += 1
        print(f"  [fuente inactiva] {fuente.nombre}")
        return

    html = descargar(url)
    if not html:
        return                          # error de red: no se registra nada, se reintenta
    nota = extraer_nota(html)

    relevante = bool(nota["texto"]) and (
        not config.FILTRO_PREVIO or parece_relevante(nota["titulo"], nota["texto"]))
    noticia = guardar.registrar_noticia(url, fuente, nota, relevante) if config.GUARDAR_EN_BD else None

    if not relevante:
        stats["sin_texto" if not nota["texto"] else "filtro_previo"] += 1
        cerrar(url, noticia, Estado.DESCARTADA)
        return

    crudos = extraer_avistamientos(nota["titulo"], nota["fecha"], nota["texto"])
    if crudos is None:                  # la IA falló: la nota queda en ERROR y se reintenta
        stats["ia_error"] += 1
        cerrar(url, noticia, Estado.ERROR)
        return
    if not crudos:
        stats["sin_avistamientos"] += 1
        print("  [IA] la nota no reporta avistamientos")
        cerrar(url, noticia, Estado.DESCARTADA)
        return

    resultados = Counter()
    for raw in crudos:
        stats["avistamientos_ia"] += 1
        resultado = procesar_avistamiento(raw, nota, noticia, url)
        resultados[resultado] += 1
        stats[resultado] += 1

    if resultados["creado"] or resultados["solo_csv"]:
        estado = Estado.PROCESADA
    elif resultados["error"]:
        estado = Estado.ERROR           # falló al guardar: se reintenta en la próxima corrida
    else:
        estado = Estado.DESCARTADA      # nada nuevo para guardar
    cerrar(url, noticia, estado)


def procesar_avistamiento(raw: dict, nota: dict, noticia, url: str) -> str:
    """Valida, geocodifica, puntúa y guarda un avistamiento. Devuelve el resultado."""
    fila = {"url": url, "titulo": nota["titulo"], "nombre_comun": raw.get("nombre_comun"),
            "nombre_cientifico": raw.get("nombre_cientifico"), "lugar_texto": raw.get("lugar_texto"),
            "fecha": raw.get("fecha_avistamiento"), "cita": raw.get("cita")}

    def descartar(motivo: str) -> str:
        print(f"  [descartado] {raw.get('nombre_comun')}: {motivo}")
        guardar.registrar_csv({**fila, "resultado": f"descartado: {motivo}"})
        return "descartado_" + motivo.split(" (")[0].replace(" ", "_")

    av, motivo = normalizar_avistamiento(raw, nota["fecha"])
    if av is None:
        return descartar(motivo)
    fila["fecha"] = av["fecha"]

    geo = geocodificar(av["lugar_texto"]) if av["lugar_texto"] else None
    if geo is None and config.REQUIERE_UBICACION:
        return descartar(f"no se pudo ubicar '{av['lugar_texto']}' en Misiones")
    if geo:
        fila.update(lat=geo.lat, lon=geo.lon, metodo_geo=geo.metodo)

    especie = guardar.buscar_especie(av["candidatos_cientificos"], av["nombre_comun"])
    if especie is None:
        faltante = av["candidatos_cientificos"][0] if av["candidatos_cientificos"] else av["nombre_comun"]
        guardar.ESPECIES_NO_CARGADAS.add(faltante)
        if config.REQUIERE_ESPECIE_PARA_BD:
            return descartar(f"especie no cargada ({faltante})")

    confianza, controles = calcular_confianza(av, nota["texto"], especie is not None, geo)
    fila["confianza"] = confianza
    if confianza < config.CONFIANZA_MINIMA:
        return descartar(f"confianza baja ({confianza})")

    if not config.GUARDAR_EN_BD:
        resultado = "solo_csv"
    elif guardar.es_duplicado(especie, av["fecha"], geo):
        resultado = "duplicado"
    else:
        try:
            guardar.guardar_avistamiento(noticia, av, especie, geo, confianza,
                                         json_extraido={**raw, "_controles": controles})
            resultado = "creado"
        except Exception as e:          # el registro no se guarda (todo o nada), el resto sigue
            print(f"  [BD error] {type(e).__name__}: {e}")
            resultado = "error"

    donde = f"{av['lugar_texto'] or 'sin lugar'} ({geo.metodo if geo else 'sin ubicar'})"
    sin_especie = "" if especie else " | sin especie en catálogo"
    print(f"  -> {av['nombre_comun']} @ {donde} | confianza {confianza}{sin_especie}: {resultado}")
    guardar.registrar_csv({**fila, "resultado": resultado})
    return resultado


def cerrar(url: str, noticia, estado):
    """Con base de datos actualiza el estado de la noticia; sin ella usa el archivo de texto."""
    if noticia is not None:
        guardar.cerrar_noticia(noticia, estado)
    elif estado != Estado.ERROR:
        marcar_procesada(url)


if __name__ == "__main__":
    main()
