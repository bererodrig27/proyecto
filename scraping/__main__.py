"""Orquestador. Ejecutar desde la carpeta de manage.py:  python -m scraping"""
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scraping import config  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", config.DJANGO_SETTINGS_MODULE)
import django  # noqa: E402
django.setup()

from scraping.descubrir import descubrir, marcar_procesada  # noqa: E402
from scraping.descargar import descargar, extraer_nota, ESTADISTICAS as EST_DESCARGA  # noqa: E402
from scraping.extraer_ia import extraer_avistamientos, parece_relevante  # noqa: E402
from scraping.normalizar import normalizar_avistamiento  # noqa: E402
from scraping.geocodificar import geocodificar  # noqa: E402
from scraping import guardar  # noqa: E402


def main():
    stats = Counter()
    notas = descubrir()

    for i, (fuente, url) in enumerate(notas, 1):
        print(f"[{i}/{len(notas)}] {url}")
        html = descargar(url)
        if not html:
            continue                                   # error de red: se reintenta otra vez
        nota = extraer_nota(html)

        if not nota["texto"]:
            stats["sin_texto"] += 1
            marcar_procesada(url)
            continue
        if config.FILTRO_PREVIO and not parece_relevante(nota["titulo"], nota["texto"]):
            stats["filtro_previo"] += 1
            marcar_procesada(url)
            continue

        crudos = extraer_avistamientos(nota["titulo"], nota["fecha"], nota["texto"])
        if crudos is None:
            stats["ia_error"] += 1                     # no se marca: reintento futuro
            continue
        marcar_procesada(url)
        if not crudos:
            stats["sin_avistamientos"] += 1
            print("  [IA] sin avistamientos")
            continue

        for raw in crudos:
            stats["avistamientos_ia"] += 1
            av, motivo = normalizar_avistamiento(raw, nota["fecha"])
            fila = {"url": url, "titulo": nota["titulo"], "nombre_comun": raw.get("nombre_comun"),
                    "nombre_cientifico": raw.get("nombre_cientifico"),
                    "lugar_texto": raw.get("lugar_texto"), "fecha": raw.get("fecha_avistamiento"),
                    "confianza": raw.get("confianza"), "cita": raw.get("cita")}
            if av is None:
                stats["descartado_" + motivo.split(" (")[0].replace(" ", "_")] += 1
                print(f"  [descartado] {raw.get('nombre_comun')}: {motivo}")
                registrar_csv_seguro(fila, f"descartado: {motivo}")
                continue

            geo = geocodificar(av["lugar_texto"])
            if geo is None:
                stats["sin_coordenadas"] += 1
                print(f"  [descartado] no se pudo geocodificar '{av['lugar_texto']}' en Misiones")
                registrar_csv_seguro(fila, "descartado: sin coordenadas")
                continue
            lat, lon, metodo = geo
            fila.update(lat=lat, lon=lon, metodo_geo=metodo, fecha=av["fecha"])

            if config.GUARDAR_EN_BD:
                resultado = guardar.guardar_registro(av, lat, lon, url)
            else:
                resultado = "solo_csv"
            stats[resultado] += 1
            print(f"  -> {av['nombre_comun']} @ {av['lugar_texto']} ({metodo}): {resultado}")
            registrar_csv_seguro(fila, resultado)

    print(f"\nResumen: {dict(stats)} | descargas: {EST_DESCARGA}")
    if guardar.ESPECIES_NO_CARGADAS:
        print("Especies que faltan en la tabla `especie`: "
              + ", ".join(sorted(guardar.ESPECIES_NO_CARGADAS)))


def registrar_csv_seguro(fila: dict, resultado: str):
    guardar.registrar_csv({**fila, "resultado": resultado})


if __name__ == "__main__":
    main()