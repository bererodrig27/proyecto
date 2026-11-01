"""
Carga ~50 especies de fauna de la biodiversidad de Misiones (Selva Paranaense).


Ubicación del archivo:
    MisionaBio/management/commands/cargar_especies.py


Uso (dentro de Docker):
    docker compose exec web python manage.py cargar_especies
    docker compose exec web python manage.py cargar_especies --actualizar
    docker compose exec web python manage.py cargar_especies --simular


- Es idempotente: si el nombre científico ya existe, no lo duplica ni lo pisa
  (salvo que se use --actualizar).
- Todo corre en una transacción: si algo falla, no se guarda nada.
- Los estados de conservación corresponden a la Lista Roja UICN (evaluación
  global) y conviene verificarlos en https://www.iucnredlist.org/ antes de
  usarlos como dato oficial. A nivel nacional varios cambian (por ejemplo, el
  yaguareté está En peligro crítico en Argentina).
"""
from django.core.management.base import BaseCommand
from django.db import transaction


from MisionaBio.models import Especie, normalizar_nombre_cientifico


# (nombre_cientifico, nombre_comun, clase, orden, familia, estado_uicn, descripcion)
ESPECIES = [
    # ------------------------------------------------------------------ Mammalia
    ("Panthera onca", "Yaguareté", "Mammalia", "Carnivora", "Felidae", "NT",
     "Mayor felino de América y depredador tope de la selva. En Argentina quedan pocos ejemplares, sobre todo en Misiones, y es Monumento Natural Nacional."),
    ("Puma concolor", "Puma", "Mammalia", "Carnivora", "Felidae", "LC",
     "Felino grande de pelaje uniforme y cola larga. Muy adaptable, habita desde la selva hasta zonas abiertas."),
    ("Leopardus pardalis", "Ocelote", "Mammalia", "Carnivora", "Felidae", "LC",
     "Felino mediano de pelaje manchado en forma de cadenas. De hábitos nocturnos, caza roedores y aves."),
    ("Leopardus wiedii", "Margay (gato tigre)", "Mammalia", "Carnivora", "Felidae", "NT",
     "Felino pequeño y arborícola, de ojos grandes y cola larga. Puede descender de los árboles cabeza abajo."),
    ("Leopardus guttulus", "Tirica (gato tigre chico)", "Mammalia", "Carnivora", "Felidae", "VU",
     "Pequeño felino manchado de la Selva Paranaense, de hábitos nocturnos. Amenazado por la pérdida de selva."),
    ("Herpailurus yagouaroundi", "Yaguarundí (gato moro)", "Mammalia", "Carnivora", "Felidae", "LC",
     "Felino de cuerpo alargado y pelaje de un solo color, de hábitos diurnos, muy ligado a la vegetación densa."),
    ("Tapirus terrestris", "Tapir (anta)", "Mammalia", "Perissodactyla", "Tapiridae", "VU",
     "Mayor mamífero terrestre de la selva, de hocico alargado y móvil. Dispersa semillas y depende de cursos de agua."),
    ("Myrmecophaga tridactyla", "Oso hormiguero gigante (yurumí)", "Mammalia", "Pilosa", "Myrmecophagidae", "VU",
     "Mamífero de hocico muy largo y cola frondosa, que se alimenta de hormigas y termitas. Sin dientes."),
    ("Tamandua tetradactyla", "Oso melero", "Mammalia", "Pilosa", "Myrmecophagidae", "LC",
     "Oso hormiguero mediano de cola prensil, que camina por el suelo y trepa a los árboles."),
    ("Chrysocyon brachyurus", "Aguará guazú", "Mammalia", "Carnivora", "Canidae", "NT",
     "Mayor cánido de Sudamérica, de patas largas y pelaje rojizo con crin oscura. Omnívoro, come frutos como la lobeira."),
    ("Cerdocyon thous", "Zorro de monte (aguará-í)", "Mammalia", "Carnivora", "Canidae", "LC",
     "Zorro de pelaje grisáceo y patas oscuras, omnívoro y muy común en ambientes de monte."),
    ("Nasua nasua", "Coatí", "Mammalia", "Carnivora", "Procyonidae", "LC",
     "Mamífero social de hocico alargado y cola anillada, que se mueve en grupos y busca invertebrados y frutos."),
    ("Procyon cancrivorus", "Mapache cangrejero (aguará popé)", "Mammalia", "Carnivora", "Procyonidae", "LC",
     "Mapache de hábitos nocturnos asociado al agua, de antifaz facial y manos hábiles."),
    ("Eira barbara", "Hurón mayor (irara)", "Mammalia", "Carnivora", "Mustelidae", "LC",
     "Mustélido ágil de cuerpo oscuro y cabeza clara, que caza en el suelo y en los árboles."),
    ("Lontra longicaudis", "Lobito de río (nutria neotropical)", "Mammalia", "Carnivora", "Mustelidae", "NT",
     "Nutria de ríos y arroyos, muy buena nadadora. Es indicadora de la buena calidad del agua."),
    ("Pecari tajacu", "Pecarí de collar (chancho moro)", "Mammalia", "Artiodactyla", "Tayassuidae", "LC",
     "Mamífero parecido a un cerdo, con una banda clara en torno al cuello. Vive en grupos."),
    ("Tayassu pecari", "Pecarí labiado (baguá)", "Mammalia", "Artiodactyla", "Tayassuidae", "VU",
     "Pecarí de gran tamaño que forma manadas numerosas y recorre grandes áreas de selva."),
    ("Mazama americana", "Corzuela roja", "Mammalia", "Artiodactyla", "Cervidae", "DD",
     "Cérvido de pelaje rojizo y astas simples, de hábitos solitarios y selváticos."),
    ("Mazama nana", "Corzuela enana", "Mammalia", "Artiodactyla", "Cervidae", "VU",
     "El menor de los cérvidos de la selva misionera, de pelaje pardo grisáceo y hábitos muy esquivos."),
    ("Alouatta caraya", "Carayá negro", "Mammalia", "Primates", "Atelidae", "LC",
     "Mono aullador cuyo canto se escucha a gran distancia. Los machos son negros y las hembras, amarillentas."),
    ("Sapajus nigritus", "Mono caí", "Mammalia", "Primates", "Cebidae", "NT",
     "Mono de cola prensil y copete en la cabeza, muy hábil con las manos. Vive en grupos y es omnívoro."),
    ("Dasypus novemcinctus", "Mulita grande (tatú)", "Mammalia", "Cingulata", "Dasypodidae", "LC",
     "Armadillo de caparazón articulado en bandas, excavador y de hábitos nocturnos."),
    ("Cabassous tatouay", "Cabasú chico (tatú de rabo molle)", "Mammalia", "Cingulata", "Chlamyphoridae", "LC",
     "Armadillo de cola desnuda y garras grandes, que se alimenta de hormigas y termitas."),
    ("Hydrochoerus hydrochaeris", "Carpincho", "Mammalia", "Rodentia", "Caviidae", "LC",
     "El roedor más grande del mundo, semiacuático y social, que vive cerca de ríos y arroyos."),
    ("Cuniculus paca", "Paca", "Mammalia", "Rodentia", "Cuniculidae", "LC",
     "Roedor grande de pelaje pardo con hileras de manchas claras. De hábitos nocturnos y asociado a cursos de agua."),
    ("Dasyprocta azarae", "Agutí", "Mammalia", "Rodentia", "Dasyproctidae", "DD",
     "Roedor de patas largas y pelaje dorado a pardo. Entierra semillas y colabora con la regeneración de la selva."),


    # --------------------------------------------------------------------- Aves
    ("Ramphastos toco", "Tucán grande", "Aves", "Piciformes", "Ramphastidae", "LC",
     "El mayor de los tucanes, de pico anaranjado con una mancha negra en la punta."),
    ("Ramphastos dicolorus", "Tucán pico verde", "Aves", "Piciformes", "Ramphastidae", "LC",
 "Tucán de pico verdoso y pecho amarillo, típico de la selva misionera. Come frutos y dispersa semillas."),
    ("Pteroglossus castanotis", "Arasarí castaño", "Aves", "Piciformes", "Ramphastidae", "LC",
     "Pequeño tucán de cola larga, pico bicolor y plumaje con bandas. Se desplaza en grupos."),
    ("Harpia harpyja", "Harpía (águila harpía)", "Aves", "Accipitriformes", "Accipitridae", "NT",
     "Una de las águilas más grandes del mundo, con doble cresta de plumas. Muy rara en Misiones."),
    ("Spizaetus ornatus", "Águila monera (águila crestuda real)", "Aves", "Accipitriformes", "Accipitridae", "NT",
     "Águila de selva con cresta, pecho barrado y plumaje vistoso. Caza aves y mamíferos medianos."),
    ("Amazona vinacea", "Loro vinoso", "Aves", "Psittaciformes", "Psittacidae", "EN",
     "Loro verde de pecho color vino, ligado a los bosques con pinos y araucarias. Amenazado por la pérdida de hábitat y el tráfico."),
    ("Amazona pretrei", "Charao", "Aves", "Psittaciformes", "Psittacidae", "VU",
     "Loro de hombros rojos que realiza migraciones estacionales y depende de los pinares de araucaria."),
    ("Primolius maracana", "Maracaná lomo rojo", "Aves", "Psittaciformes", "Psittacidae", "NT",
     "Guacamayo pequeño de plumaje verde con una mancha roja en el vientre y el dorso."),
    ("Pyrrhura frontalis", "Chiripepé cabeza verde", "Aves", "Psittaciformes", "Psittacidae", "LC",
     "Cotorra pequeña de cola larga rojiza, que se mueve en bandadas ruidosas."),
    ("Aburria jacutinga", "Yacutinga", "Aves", "Galliformes", "Cracidae", "EN",
     "Ave grande de la selva con una mancha blanca en la cabeza y barbilla roja. Es una importante dispersora de semillas y está muy amenazada por la caza."),
    ("Penelope obscura", "Yacupoí (pava de monte)", "Aves", "Galliformes", "Cracidae", "LC",
     "Ave grande de cola larga que vive en el dosel de la selva y se alimenta de frutos."),
    ("Tinamus solitarius", "Macuco", "Aves", "Tinamiformes", "Tinamidae", "NT",
     "Ave del suelo de la selva, de camuflaje pardo y vuelo breve. Es esquiva y muy perseguida por la caza."),
    ("Procnias nudicollis", "Pájaro campana", "Aves", "Passeriformes", "Cotingidae", "VU",
     "El macho es blanco con la garganta verde desnuda y emite un canto metálico muy potente."),
    ("Trogon surrucura", "Surucuá común", "Aves", "Trogoniformes", "Trogonidae", "LC",
     "Ave de colores vivos con pecho rojo y dorso azulado, que se posa inmóvil en ramas medias de la selva."),
    ("Chiroxiphia caudata", "Bailarín azul", "Aves", "Passeriformes", "Pipridae", "LC",
     "Pequeño pájaro cuyos machos realizan danzas grupales para cortejar. Plumaje azul y rojo en el macho."),
    ("Cyanocorax chrysops", "Urraca común", "Aves", "Passeriformes", "Corvidae", "LC",
     "Córvido de cabeza oscura, ceja celeste y pecho amarillento, social y muy ruidoso."),


    # ------------------------------------------------------------------ Reptilia
    ("Caiman latirostris", "Yacaré overo", "Reptilia", "Crocodylia", "Alligatoridae", "LC",
     "Caimán de hocico ancho y coloración amarillenta con manchas. Habita ríos, esteros y lagunas."),
    ("Salvator merianae", "Lagarto overo (teyú)", "Reptilia", "Squamata", "Teiidae", "LC",
     "Gran lagarto de cuerpo blanco y negro, muy activo y omnívoro. Hiberna durante el invierno."),
    ("Bothrops jararaca", "Yarará", "Reptilia", "Squamata", "Viperidae", "LC",
     "Víbora venenosa de pelaje pardo con manchas triangulares. Importante para la salud pública en la región."),
    ("Micrurus corallinus", "Coral", "Reptilia", "Squamata", "Elapidae", "LC",
     "Serpiente venenosa de anillos rojos, negros y blancos, de hábitos subterráneos."),
    ("Crotalus durissus", "Cascabel", "Reptilia", "Squamata", "Viperidae", "LC",
     "Víbora venenosa con un cascabel en la cola, que advierte con su sonido cuando se siente amenazada."),
    ("Hydromedusa tectifera", "Tortuga cuello de víbora", "Reptilia", "Testudines", "Chelidae", "LC",
     "Tortuga acuática de cuello muy largo, que se pliega de costado bajo el caparazón."),


    # ----------------------------------------------------------------- Amphibia
    ("Boana faber", "Rana martillo", "Amphibia", "Anura", "Hylidae", "LC",
     "Rana arborícola grande, cuyo canto recuerda a golpes de martillo. Construye nidos de barro en los charcos."),
    ("Leptodactylus latrans", "Rana criolla", "Amphibia", "Anura", "Leptodactylidae", "LC",
     "Rana grande y robusta, muy común en lagunas, arroyos y zonas inundables."),
    ("Rhinella icterica", "Sapo común (cururú)", "Amphibia", "Anura", "Bufonidae", "LC",
     "Sapo grande de piel rugosa con glándulas que segregan sustancias tóxicas. Muy visible en jardines y selva."),
]




class Command(BaseCommand):
    help = "Carga especies de fauna de Misiones en la tabla Especie (idempotente)."


    def add_arguments(self, parser):
        parser.add_argument(
            "--actualizar",
            action="store_true",
            help="Si la especie ya existe, actualiza sus datos con los de este script.",
        )
        parser.add_argument(
            "--simular",
            action="store_true",
            help="Muestra qué se haría pero no guarda nada en la base.",
        )


    def handle(self, *args, **options):
        actualizar = options["actualizar"]
        simular = options["simular"]
        creadas = actualizadas = existentes = 0


        with transaction.atomic():
            for cientifico, comun, clase, orden, familia, estado, descripcion in ESPECIES:
                nombre = normalizar_nombre_cientifico(cientifico)
                datos = {
                    "nombre_comun": comun,
                    "clase_taxonomica": clase,
                    "taxonomia": f"Animalia > Chordata > {clase} > {orden} > {familia}",
                    "estado_conservacion": estado,
                    "descripcion": descripcion,
                }
                especie = Especie.objects.filter(nombre_cientifico=nombre).first()


                if especie is None:
                    Especie.objects.create(nombre_cientifico=nombre, **datos)
                    creadas += 1
                    self.stdout.write(f"  + {nombre} ({comun})")
                elif actualizar:
                    for campo, valor in datos.items():
                        setattr(especie, campo, valor)
                    especie.save()
                    actualizadas += 1
                    self.stdout.write(f"  ~ {nombre} actualizada")
                else:
                    existentes += 1


            if simular:
                transaction.set_rollback(True)


        prefijo = "[SIMULACIÓN] " if simular else ""
        self.stdout.write(self.style.SUCCESS(
            f"{prefijo}Listo: {creadas} creadas, {actualizadas} actualizadas, "
            f"{existentes} ya existían (total en script: {len(ESPECIES)})."
        ))
