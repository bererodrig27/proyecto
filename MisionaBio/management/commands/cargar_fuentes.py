"""
Carga en la tabla fuente los medios configurados en scraping/config.py, para que aparezcan en
el filtro del listado de avistamientos y en el formulario de carga aunque todavía no se haya
scrapeado ninguna nota de ellos.

Ubicación del archivo:
    MisionaBio/management/commands/cargar_fuentes.py

Uso (dentro de Docker):
    docker compose exec web python manage.py cargar_fuentes
    docker compose exec web python manage.py cargar_fuentes --simular

- Es idempotente: si el medio ya existe, no lo duplica (solo completa su URL base si está vacía).
- Si existe una fuente guardada con el dominio como nombre (por ejemplo "elterritorio.com.ar",
  que creó el scraping antes de tener nombres de medios), se RENOMBRA en vez de crear otra: así
  conserva sus noticias y avistamientos.
- Si existen las dos (la del dominio y la del nombre), no se tocan y se avisa: hay que
  unificarlas a mano porque noticia -> fuente usa PROTECT.
- Todo corre en una transacción: si algo falla, no se guarda nada.
"""
from urllib.parse import urlparse

from django.core.management.base import BaseCommand
from django.db import transaction

from MisionaBio.models import Fuente
from scraping import config


def medios_configurados():
    """{nombre: url_base} de todos los medios del scraping (búsqueda WordPress + solo RSS)."""
    return {**config.MEDIOS_SOLO_FEED, **config.SITIOS_WP}


def _dominio(url):
    return urlparse(url).netloc.replace("www.", "")


class Command(BaseCommand):
    help = "Crea (o completa) las fuentes de los medios configurados en scraping/config.py."

    def add_arguments(self, parser):
        parser.add_argument("--simular", action="store_true", help="Muestra qué haría, sin guardar.")

    def handle(self, *args, **options):
        simular = options["simular"]
        contadores = {"creadas": 0, "renombradas": 0, "existentes": 0, "conflictos": 0}

        with transaction.atomic():
            for nombre, url in medios_configurados().items():
                dominio = _dominio(url)
                fuente = Fuente.objects.filter(nombre=nombre).first()
                por_dominio = Fuente.objects.filter(nombre=dominio).first()

                if fuente and por_dominio and fuente.pk != por_dominio.pk:
                    contadores["conflictos"] += 1
                    self.stdout.write(self.style.WARNING(
                        f"  ! {nombre}: existen «{nombre}» y «{dominio}». Unificalas a mano."
                    ))
                    continue

                if fuente:
                    if not fuente.url_base:
                        fuente.url_base = url
                        fuente.save(update_fields=["url_base"])
                    contadores["existentes"] += 1
                    self.stdout.write(f"  = {nombre}")
                elif por_dominio:
                    self.stdout.write(f"  ~ {dominio} -> {nombre}")
                    por_dominio.nombre = nombre
                    if not por_dominio.url_base:
                        por_dominio.url_base = url
                    por_dominio.save(update_fields=["nombre", "url_base"])
                    contadores["renombradas"] += 1
                else:
                    self.stdout.write(self.style.SUCCESS(f"  + {nombre}"))
                    Fuente.objects.create(nombre=nombre, url_base=url, activa=True)
                    contadores["creadas"] += 1

            if simular:
                transaction.set_rollback(True)

        self.stdout.write(
            ("[SIMULACIÓN, no se guardó nada] " if simular else "")
            + "Fuentes: {creadas} creadas, {renombradas} renombradas, "
              "{existentes} ya estaban, {conflictos} con conflicto.".format(**contadores)
        )