"""Completa ubicacion.departamento en las ubicaciones que ya estaban cargadas.

Uso (desde la carpeta de manage.py), después de migrar:   python -m scraping.rellenar_departamentos
Usa solo el diccionario de lugares de config.COORDENADAS (sin red). Las ubicaciones que no
reconoce quedan vacías: se pueden completar a mano desde el administrador.
"""
import os
import sys
from importlib import import_module
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scraping import config  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", config.DJANGO_SETTINGS_MODULE)
import django  # noqa: E402
django.setup()

from scraping.geocodificar import desde_diccionario  # noqa: E402
from scraping.normalizar import normalizar_texto, normalizar_departamento  # noqa: E402

Ubicacion = import_module(f"{config.APP_MODELOS}.models").Ubicacion


def main():
    completadas = sin_resolver = 0
    for u in Ubicacion.objects.filter(departamento=""):
        depto = ""
        for texto in (u.nombre_lugar, u.zona):
            hallado = desde_diccionario(normalizar_texto(texto))
            depto = (hallado[2] if hallado else "") or normalizar_departamento(texto)
            if depto:
                break
        if depto:
            u.departamento = depto
            u.save(update_fields=["departamento"])
            completadas += 1
        else:
            sin_resolver += 1
            print(f"  sin departamento: {u.nombre_lugar!r} ({u.latitud}, {u.longitud})")
    print(f"Completadas: {completadas} | sin resolver: {sin_resolver}")


if __name__ == "__main__":
    main()
