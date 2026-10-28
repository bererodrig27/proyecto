"""
Descarga los límites provinciales de Georef (IGN) y guarda solo Misiones.

Se puede ejecutar desde cualquier carpeta del proyecto (busca manage.py hacia arriba):
    python generar_geojson_misiones.py

Resultado: MisionaBio/static/MisionaBio/geo/misiones.geojson
"""
import json
import urllib.request
from pathlib import Path

# Se prueban en orden hasta encontrar Misiones.
URLS = [
    "https://apis.datos.gob.ar/georef/api/v2.0/provincias.geojson",
    "https://apis.datos.gob.ar/georef/api/v2.0/provincias.geojson?max=100",
]


def carpeta_app():
    """
    Devuelve la carpeta MisionaBio/ sin depender de dónde esté el script ni de desde
    dónde se ejecute: sube por las carpetas hasta encontrar manage.py (raíz del proyecto).
    """
    for carpeta in [Path(__file__).resolve().parent, *Path(__file__).resolve().parents]:
        if (carpeta / "manage.py").is_file() and (carpeta / "MisionaBio").is_dir():
            return carpeta / "MisionaBio"
    raise SystemExit("No encuentro la raíz del proyecto (manage.py). Poné el script dentro del proyecto.")


def es_misiones(feature):
    props = feature.get("properties", {})
    return str(props.get("id", "")) == "54" or props.get("nombre") == "Misiones"


def descargar():
    for url in URLS:
        try:
            print(f"Descargando {url} ...")
            req = urllib.request.Request(url, headers={"User-Agent": "MisionaBio/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.load(resp)
        except Exception as exc:  # red, SSL, JSON inválido…
            print(f"  falló: {exc}")
            continue
        features = [f for f in data.get("features", []) if es_misiones(f)]
        if features:
            return features
        ejemplo = data["features"][0]["properties"] if data.get("features") else {}
        print(f"  no vino Misiones ({len(data.get('features', []))} regiones). Propiedades de ejemplo: {ejemplo}")
    raise SystemExit("No se pudo obtener el contorno de Misiones. Revisá tu conexión y volvé a intentar.")


def main():
    features = descargar()

    # ECharts identifica cada región por properties.name.
    for f in features:
        f["properties"] = {"name": "Misiones"}

    destino = carpeta_app() / "static" / "MisionaBio" / "geo" / "misiones.geojson"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
        encoding="utf-8",
    )
    tipo = features[0]["geometry"]["type"]
    print(f"OK: {destino} ({destino.stat().st_size / 1024:.0f} KB, geometría {tipo})")


if __name__ == "__main__":
    main()