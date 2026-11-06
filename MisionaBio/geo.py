# MisionaBio/geo.py
"""
Departamento de Misiones a partir de unas coordenadas.

Usa MisionaBio/static/MisionaBio/geo/misiones_departamentos.geojson, que se genera con
`python generar_geojson_departamentos.py`. Si el archivo no existe (o está dañado) nada
se rompe: las funciones devuelven "sin dato" y el formulario pide el departamento a mano.
"""
import json
from functools import lru_cache
from pathlib import Path

RUTA_DEPARTAMENTOS = (
    Path(__file__).resolve().parent / 'static' / 'MisionaBio' / 'geo' / 'misiones_departamentos.geojson'
)

# Un punto sobre la costa (los ríos Paraná, Iguazú y Uruguay son el límite de la provincia)
# puede caer unos metros fuera del polígono. Hasta esta distancia (grados, ~3 km) se asigna
# el departamento más cercano en vez de no devolver nada.
TOLERANCIA_COSTA = 0.03


@lru_cache(maxsize=1)
def _cargar():
    """Lee el GeoJSON una sola vez por proceso: [(nombre, bbox, [poligonos])]."""
    try:
        datos = json.loads(RUTA_DEPARTAMENTOS.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return ()
    departamentos = []
    for f in datos.get('features', []):
        geom = f.get('geometry') or {}
        props = f.get('properties') or {}
        nombre = props.get('departamento') or props.get('name')
        if geom.get('type') == 'Polygon':
            poligonos = [geom['coordinates']]
        elif geom.get('type') == 'MultiPolygon':
            poligonos = geom['coordinates']
        else:
            continue
        if not nombre or not poligonos:
            continue
        xs = [p[0] for pol in poligonos for p in pol[0]]
        ys = [p[1] for pol in poligonos for p in pol[0]]
        departamentos.append((nombre, (min(xs), min(ys), max(xs), max(ys)), poligonos))
    return tuple(departamentos)


def hay_datos():
    """True si el archivo de departamentos está disponible."""
    return bool(_cargar())


def _dentro_de_anillo(x, y, anillo):
    """Ray casting: cuenta cuántas veces un rayo hacia la derecha cruza el borde."""
    dentro = False
    j = len(anillo) - 1
    for i in range(len(anillo)):
        xi, yi = anillo[i][0], anillo[i][1]
        xj, yj = anillo[j][0], anillo[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            dentro = not dentro
        j = i
    return dentro


def _dentro_de_poligono(x, y, poligono):
    """Dentro del anillo exterior y fuera de los agujeros."""
    if not _dentro_de_anillo(x, y, poligono[0]):
        return False
    return not any(_dentro_de_anillo(x, y, hueco) for hueco in poligono[1:])


def _distancia_a_anillo(x, y, anillo):
    mejor = float('inf')
    for i in range(len(anillo) - 1):
        ax, ay = anillo[i][0], anillo[i][1]
        bx, by = anillo[i + 1][0], anillo[i + 1][1]
        dx, dy = bx - ax, by - ay
        largo2 = dx * dx + dy * dy
        t = 0.0 if largo2 == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / largo2))
        d = ((x - (ax + t * dx)) ** 2 + (y - (ay + t * dy)) ** 2) ** 0.5
        mejor = min(mejor, d)
    return mejor


def departamento_de(lat, lon):
    """
    Devuelve (nombre, exacto) o (None, False).
      - exacto=True : el punto está dentro del polígono del departamento.
      - exacto=False: cae a menos de TOLERANCIA_COSTA de un departamento (costa de un río);
                      sirve para completar el dato pero no para contradecir al usuario.
    """
    departamentos = _cargar()
    if not departamentos or lat is None or lon is None:
        return None, False
    x, y = float(lon), float(lat)

    for nombre, (x0, y0, x1, y1), poligonos in departamentos:
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            continue
        if any(_dentro_de_poligono(x, y, p) for p in poligonos):
            return nombre, True

    cercano, distancia = None, TOLERANCIA_COSTA
    for nombre, _bbox, poligonos in departamentos:
        for p in poligonos:
            d = _distancia_a_anillo(x, y, p[0])
            if d <= distancia:
                cercano, distancia = nombre, d
    return (cercano, False) if cercano else (None, False)

# -----------------------------------------------------------------------------
# Punto aproximado de un departamento (para completar latitud/longitud sin que la
# persona tenga que conocerlas).
# -----------------------------------------------------------------------------

def _area_firmada(anillo):
    """Fórmula del cordón (shoelace). Sirve para elegir el polígono más grande."""
    return sum(
        anillo[i][0] * anillo[(i + 1) % len(anillo)][1] - anillo[(i + 1) % len(anillo)][0] * anillo[i][1]
        for i in range(len(anillo))
    ) / 2


def _cortes_horizontales(y, poligono):
    """Abscisas donde la recta horizontal `y` cruza los bordes del polígono (anillos y agujeros)."""
    xs = []
    for anillo in poligono:
        for i in range(len(anillo) - 1):
            x0, y0 = anillo[i][0], anillo[i][1]
            x1, y1 = anillo[i + 1][0], anillo[i + 1][1]
            if (y0 > y) != (y1 > y):
                xs.append(x0 + (y - y0) * (x1 - x0) / (y1 - y0))
    return sorted(xs)


def _punto_interior(poligono):
    """
    Punto que SIEMPRE cae dentro del polígono, cerca de su centro. No se usa el centroide
    matemático porque en departamentos alargados o cóncavos (Iguazú, Eldorado, San Pedro…)
    puede quedar afuera. Se recorren varias rectas horizontales cerca del centro y se toma el
    punto medio del tramo interior más ancho.
    """
    exterior = poligono[0]
    ys = [p[1] for p in exterior]
    y_min, y_max = min(ys), max(ys)
    alto = y_max - y_min
    centro_y = (y_min + y_max) / 2
    mejor, ancho_mejor = None, -1.0
    for k in range(-10, 11):
        y = centro_y + alto * 0.2 * k / 10
        xs = _cortes_horizontales(y, poligono)
        for a, b in zip(xs[0::2], xs[1::2]):
            # Se prefiere el tramo más ancho; a igualdad, el más cercano al centro vertical.
            ancho = (b - a) - abs(y - centro_y) * 0.1
            if ancho > ancho_mejor:
                mejor, ancho_mejor = ((a + b) / 2, y), ancho
    if mejor and _dentro_de_poligono(mejor[0], mejor[1], poligono):
        return mejor
    return None


@lru_cache(maxsize=1)
def centros_departamentos():
    """
    {departamento: (latitud, longitud)}: un punto interior por departamento, redondeado a
    4 decimales (~10 m; de sobra para algo que se guarda como precisión APROXIMADA).
    En departamentos con varios polígonos (Capital tiene islas) se usa el más grande.
    Devuelve {} si falta el archivo de departamentos.
    """
    centros = {}
    for nombre, _bbox, poligonos in _cargar():
        mayor = max(poligonos, key=lambda p: abs(_area_firmada(p[0])))
        punto = _punto_interior(mayor)
        if punto:
            centros[nombre] = (round(punto[1], 4), round(punto[0], 4))
    return centros


def centro_departamento(nombre):
    """(latitud, longitud) aproximadas del departamento, o None si no se conoce."""
    return centros_departamentos().get((nombre or '').strip())