from django.core.management.base import BaseCommand
from django.db import transaction

from MisionaBio.models import ExtraccionIA, Registro, Noticia, Ubicacion, Especie


class Command(BaseCommand):
    help = "Borra especies, avistamientos, noticias y extracciones. Conserva usuarios y fuentes."

    def add_arguments(self, parser):
        parser.add_argument("--si", action="store_true", help="Confirma el borrado")

    def handle(self, *args, **options):
        if not options["si"]:
            self.stderr.write("Agregá --si para confirmar.")
            return
        # Orden obligatorio: Registro usa PROTECT hacia noticia, especie y ubicacion
        with transaction.atomic():
            for modelo in (ExtraccionIA, Registro, Noticia, Ubicacion, Especie):
                n, _ = modelo.objects.all().delete()
                self.stdout.write(f"{modelo.__name__}: {n} borrados")