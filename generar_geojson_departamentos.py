"""
Descarga los departamentos de Misiones de Georef (IGN) y los guarda simplificados.

Se usa para completar solo el departamento a partir de las coordenadas del formulario de
avistamientos y para validar que el punto caiga donde dice la persona que lo cargó.

Se puede ejecutar desde cualquier carpeta del proyecto (busca manage.py hacia arriba):
    python generar_geojson_departamentos.py

Resultado: MisionaBio/static/MisionaBio/geo/misiones_departamentos.geojson
"""
import json
import unicodedata
import urllib.request
from pathlib import Path

# Se prueban en orden hasta encontrar los departamentos de Misiones (provincia 54).
URLS = [
    "https://apis.datos.gob.ar/georef/api/v2.0/departamentos.geojson?provincia=54&max=100",
    "https://apis.datos.gob.ar/georef/api/v2.0/departamentos?provincia=54&max=100&formato=geojson",
]

# Los mismos 17 valores de Ubicacion.Departamento (models.py). Georef puede escribirlos con
# otras mayúsculas o tildes; se unifican a estos para que coincidan con la base.
DEPARTAMENTOS = [
    "Apóstoles", "Cainguás", "Candelaria", "Capital", "Concepción", "Eldorado",
    "General Manuel Belgrano", "Guaraní", "Iguazú", "Leandro N. Alem",
    "Libertador General San Martín", "Montecarlo", "Oberá", "San Ignacio",
    "San Javier", "San Pedro", "25 de Mayo",
]

DECIMALES = 4        # 4 decimales = ~11 m: sobra para decidir un departamento
TOLERANCIA = 0.0008  # simplificación (grados, ~90 m). Baja el peso del archivo sin cambiar la forma


def carpeta_app():
    """Devuelve la carpeta MisionaBio/ subiendo hasta encontrar manage.py (raíz del proyecto)."""
    for carpeta in [Path(__file__).resolve().parent, *Path(__file__).resolve().parents]:
        if (carpeta / "manage.py").is_file() and (carpeta / "MisionaBio").is_dir():
            return carpeta / "MisionaBio"
    raise SystemExit("No encuentro la raíz del proyecto (manage.py). Poné el script dentro del proyecto.")


def clave(texto):
    """'OBERÁ ' -> 'obera': para comparar nombres sin tildes ni mayúsculas."""
    s = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def es_de_misiones(feature):
    props = feature.get("properties", {})
    ident = str(props.get("id", ""))
    prov = props.get("provincia")
    prov_id = str(prov.get("id", "")) if isinstance(prov, dict) else ""
    return ident.startswith("54") or prov_id == "54"


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
        features = [f for f in data.get("features", []) if es_de_misiones(f) and f.get("geometry")]
        if features:
            return features
        ejemplo = data["features"][0]["properties"] if data.get("features") else {}
        print(f"  no vinieron departamentos de Misiones ({len(data.get('features', []))} regiones). Ejemplo: {ejemplo}")
    raise SystemExit("No se pudieron obtener los departamentos. Revisá tu conexión y volvé a intentar.")


# --- Simplificación de geometrías -------------------------------------------------------

def _distancia_a_segmento(p, a, b):
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return ((px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2) ** 0.5


def simplificar_linea(puntos, tolerancia):
    """Douglas-Peucker iterativo (sin recursión: los anillos pueden tener miles de puntos)."""
    if len(puntos) < 3:
        return list(puntos)
    mantener = [False] * len(puntos)
    mantener[0] = mantener[-1] = True
    pila = [(0, len(puntos) - 1)]
    while pila:
        i, j = pila.pop()
        mayor, indice = 0.0, None
        for k in range(i + 1, j):
            d = _distancia_a_segmento(puntos[k], puntos[i], puntos[j])
            if d > mayor:
                mayor, indice = d, k
        if indice is not None and mayor > tolerancia:
            mantener[indice] = True
            pila.extend([(i, indice), (indice, j)])
    return [p for p, m in zip(puntos, mantener) if m]


def simplificar_anillo(anillo):
    """Redondea, simplifica y cierra un anillo. Devuelve None si queda degenerado (<4 puntos)."""
    puntos = [[round(x, DECIMALES), round(y, DECIMALES)] for x, y, *_ in anillo]
    if len(puntos) > 1 and puntos[0] == puntos[-1]:
        puntos = puntos[:-1]
    puntos = simplificar_linea(puntos + [puntos[0]], TOLERANCIA)
    # sin repetidos consecutivos
    limpio = [p for i, p in enumerate(puntos) if i == 0 or p != puntos[i - 1]]
    return limpio if len(limpio) >= 4 else None


def simplificar_geometria(geom):
    tipo = geom["type"]
    poligonos = [geom["coordinates"]] if tipo == "Polygon" else geom["coordinates"]
    salida = []
    for poligono in poligonos:
        anillos = [simplificar_anillo(a) for a in poligono]
        if anillos and anillos[0]:                      # sin anillo exterior no hay polígono
            salida.append([a for a in anillos if a])    # agujeros degenerados se descartan
    if not salida:
        return None
    if len(salida) == 1:
        return {"type": "Polygon", "coordinates": salida[0]}
    return {"type": "MultiPolygon", "coordinates": salida}


def main():
    features = descargar()
    por_clave = {clave(d): d for d in DEPARTAMENTOS}

    resultado, sin_reconocer = [], []
    for f in features:
        props = f.get("properties", {})
        nombre_georef = props.get("nombre") or props.get("name") or ""
        nombre = por_clave.get(clave(nombre_georef))
        if nombre is None:
            sin_reconocer.append(nombre_georef)
            nombre = nombre_georef
        geom = simplificar_geometria(f["geometry"])
        if geom is None:
            print(f"  aviso: {nombre} quedó sin geometría válida y se omite")
            continue
        # "departamento" es lo que usa MisionaBio; "name" es lo que usa ECharts.
        resultado.append({
            "type": "Feature",
            "properties": {"departamento": nombre, "name": nombre},
            "geometry": geom,
        })

    faltan = [d for d in DEPARTAMENTOS if d not in {r["properties"]["departamento"] for r in resultado}]

    destino = carpeta_app() / "static" / "MisionaBio" / "geo" / "misiones_departamentos.geojson"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        json.dumps({"type": "FeatureCollection", "features": resultado}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"OK: {destino} ({destino.stat().st_size / 1024:.0f} KB, {len(resultado)} departamentos)")
    if sin_reconocer:
        print(f"ATENCIÓN: nombres de Georef que no coinciden con models.py: {sin_reconocer}")
    if faltan:
        print(f"ATENCIÓN: faltan departamentos de models.py en el resultado: {faltan}")


if __name__ == "__main__":
    main()