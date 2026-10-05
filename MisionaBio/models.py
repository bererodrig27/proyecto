import os
import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone


# =============================================================================
# Usuarios y roles
# =============================================================================

class Rol(models.Model):
    # Tabla "rol": id_rol (PK), nombre
    id_rol = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=50, unique=True)

    class Meta:
        db_table = 'rol'

    def __str__(self):
        return self.nombre


class Usuario(AbstractUser):
    # Mapeo contra la tabla "usuario" del modelo relacional:
    #   id_usuario      -> id (PK autoincremental que ya provee AbstractUser)
    #   nombre_usuario  -> username (heredado de AbstractUser, único)
    #   email           -> email (heredado, lo hacemos obligatorio y único)
    #   password_hash   -> password (Django lo guarda SIEMPRE hasheado, nunca en texto plano)
    #   id_rol          -> rol (FK abajo)
    #   fecha_alta      -> fecha_alta (DateField explícito, la ER pide DATE, no DATETIME)

    email = models.EmailField(unique=True, blank=False)
    rol = models.ForeignKey(
        Rol,
        on_delete=models.PROTECT,
        db_column='id_rol',
        related_name='usuarios',
    )
    fecha_alta = models.DateField(auto_now_add=True)

    class Meta:
        db_table = 'usuario'

    def es_administrador(self):
        return self.rol.nombre == 'Administrador'


# =============================================================================
# Utilidades de normalización (RF02: evitar duplicados e inconsistencias)
# =============================================================================

def _limpiar_espacios(valor):
    """Quita espacios al inicio/fin y colapsa los repetidos."""
    return ' '.join((valor or '').split())


def normalizar_nombre_cientifico(valor):
    """
    Deja el nombre científico en formato binomial estándar:
    'panthera   ONCA' -> 'Panthera onca'.
    El género va con inicial mayúscula y el resto en minúsculas.
    """
    partes = _limpiar_espacios(valor).split(' ')
    if not partes or not partes[0]:
        return ''
    partes[0] = partes[0].capitalize()
    partes[1:] = [p.lower() for p in partes[1:]]
    return ' '.join(partes)


# =============================================================================
# Fuente
# =============================================================================

class Fuente(models.Model):
    # Tabla "fuente": id_fuente, nombre, tipo, url, confiabilidad
    # Procedencia de cada registro (RF11): GBIF, iNaturalist, carga manual, etc.

    class Tipo(models.TextChoices):
        API = 'API', 'API'
        SCRAPING = 'SCRAPING', 'Scraping'
        MANUAL = 'MANUAL', 'Carga manual'

    class Confiabilidad(models.TextChoices):
        ALTA = 'ALTA', 'Alta'
        MEDIA = 'MEDIA', 'Media'
        BAJA = 'BAJA', 'Baja'

    id_fuente = models.AutoField(primary_key=True)
    nombre = models.CharField('nombre', max_length=100, unique=True)
    tipo = models.CharField('tipo', max_length=50, choices=Tipo.choices, default=Tipo.API)
    url = models.URLField('URL', max_length=255, blank=True)
    confiabilidad = models.CharField(
        'confiabilidad',
        max_length=50,
        choices=Confiabilidad.choices,
        default=Confiabilidad.MEDIA,
    )

    class Meta:
        db_table = 'fuente'
        verbose_name = 'fuente'
        verbose_name_plural = 'fuentes'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


# =============================================================================
# Especie
# =============================================================================

class Especie(models.Model):
    # Tabla "especie": id_especie, nombre_cientifico, nombre_comun,
    #                  clase_taxonomica, taxonomia, estado_conservacion
    #
    # Nota Oracle: Oracle trata el texto vacío ('') como NULL. Por eso los
    # CharField con blank=True quedan nullable en la base y Django lo maneja solo.

    class EstadoConservacion(models.TextChoices):
        # Categorías de la Lista Roja de la UICN
        NO_EVALUADA = 'NE', 'No evaluada'
        DATOS_INSUFICIENTES = 'DD', 'Datos insuficientes'
        PREOCUPACION_MENOR = 'LC', 'Preocupación menor'
        CASI_AMENAZADA = 'NT', 'Casi amenazada'
        VULNERABLE = 'VU', 'Vulnerable'
        EN_PELIGRO = 'EN', 'En peligro'
        PELIGRO_CRITICO = 'CR', 'En peligro crítico'
        EXTINTA_SILVESTRE = 'EW', 'Extinta en estado silvestre'
        EXTINTA = 'EX', 'Extinta'

    id_especie = models.AutoField(primary_key=True)
    nombre_cientifico = models.CharField(
        'nombre científico',
        max_length=150,
        unique=True,
        help_text='Formato binomial, por ejemplo: Panthera onca.',
    )
    nombre_comun = models.CharField('nombre común', max_length=150, blank=True)
    clase_taxonomica = models.CharField(
        'clase taxonómica',
        max_length=50,
        blank=True,
        help_text='Por ejemplo: Aves, Mammalia, Reptilia, Amphibia.',
    )
    taxonomia = models.CharField(
        'taxonomía',
        max_length=255,
        blank=True,
        help_text='Linaje completo, por ejemplo: Animalia > Chordata > Mammalia > Carnivora > Felidae.',
    )
    estado_conservacion = models.CharField(
        'estado de conservación',
        max_length=50,
        choices=EstadoConservacion.choices,
        default=EstadoConservacion.NO_EVALUADA,
    )

    # --- Campo sugerido (NO está en el MR actual). Ver notas: RF06 pide una
    # --- descripción en la ficha de la especie. Si lo agregan al MR, descomentar.
    # descripcion = models.TextField('descripción', blank=True)

    class Meta:
        db_table = 'especie'
        verbose_name = 'especie'
        verbose_name_plural = 'especies'
        # Orden determinista: imprescindible para paginar de forma estable.
        ordering = ['nombre_cientifico']
        indexes = [
            models.Index(fields=['nombre_comun'], name='ix_especie_comun'),
            models.Index(fields=['clase_taxonomica'], name='ix_especie_clase'),
        ]

    def __str__(self):
        if self.nombre_comun:
            return f'{self.nombre_cientifico} ({self.nombre_comun})'
        return self.nombre_cientifico

    def normalizar(self):
        self.nombre_cientifico = normalizar_nombre_cientifico(self.nombre_cientifico)
        self.nombre_comun = _limpiar_espacios(self.nombre_comun)
        self.clase_taxonomica = _limpiar_espacios(self.clase_taxonomica).capitalize()
        self.taxonomia = _limpiar_espacios(self.taxonomia)

    def clean(self):
        super().clean()
        # Se normaliza ANTES de validate_unique() para que "panthera ONCA"
        # y "Panthera onca" cuenten como el mismo nombre.
        self.normalizar()

    def save(self, *args, **kwargs):
        self.normalizar()
        super().save(*args, **kwargs)


# =============================================================================
# Registro (avistamiento)
# =============================================================================

class Registro(models.Model):
    # Tabla "registro": id_registro, id_especie, id_fuente, id_usuario_curador,
    #                   fecha_avistamiento, latitud, longitud, zona,
    #                   estado_calidad, fecha_carga
    #
    # En la interfaz se muestra como "Avistamiento" (verbose_name).

    class EstadoCalidad(models.TextChoices):
        PENDIENTE = 'PENDIENTE', 'Pendiente'
        VALIDADO = 'VALIDADO', 'Validado'
        CORREGIDO = 'CORREGIDO', 'Corregido'

    id_registro = models.AutoField(primary_key=True)

    # Nullable a propósito: un registro puede llegar con la especie incierta o
    # faltante y esperar la sugerencia de la IA (RF09).
    especie = models.ForeignKey(
        Especie,
        on_delete=models.PROTECT,
        db_column='id_especie',
        related_name='registros',
        null=True,
        blank=True,
        verbose_name='especie',
    )
    fuente = models.ForeignKey(
        Fuente,
        on_delete=models.PROTECT,
        db_column='id_fuente',
        related_name='registros',
        verbose_name='fuente',
    )
    # Nullable: los registros que vienen del scraping todavía no tienen curador.
    usuario_curador = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        db_column='id_usuario_curador',
        related_name='registros_curados',
        null=True,
        blank=True,
        verbose_name='curador',
    )

    fecha_avistamiento = models.DateField('fecha de avistamiento')
    latitud = models.DecimalField(
        'latitud',
        max_digits=9,
        decimal_places=6,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    longitud = models.DecimalField(
        'longitud',
        max_digits=9,
        decimal_places=6,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )
    zona = models.CharField('zona', max_length=100, blank=True)
    estado_calidad = models.CharField(
        'estado de calidad',
        max_length=50,
        choices=EstadoCalidad.choices,
        default=EstadoCalidad.PENDIENTE,
    )
    fecha_carga = models.DateTimeField('fecha de carga', auto_now_add=True)

    # --- Campos sugeridos (NO están en el MR actual). Ver notas. ---
    # RF03: especies sensibles con ubicación ofuscada o aproximada.
    # ubicacion_ofuscada = models.BooleanField('ubicación ofuscada', default=False)
    # RF01/RNF04: id del registro en la fuente (ej. GBIF key) para que reejecutar
    # la ingesta no duplique registros.
    # codigo_externo = models.CharField('código en la fuente', max_length=100, blank=True)

    class Meta:
        db_table = 'registro'
        verbose_name = 'avistamiento'
        verbose_name_plural = 'avistamientos'
        # Orden determinista (el id desempata): necesario para paginar bien en Oracle.
        ordering = ['-fecha_avistamiento', '-id_registro']
        indexes = [
            models.Index(fields=['fecha_avistamiento'], name='ix_registro_fecha'),
            models.Index(fields=['zona'], name='ix_registro_zona'),
            models.Index(fields=['estado_calidad'], name='ix_registro_calidad'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(latitud__gte=-90, latitud__lte=90),
                name='ck_registro_latitud',
            ),
            models.CheckConstraint(
                condition=models.Q(longitud__gte=-180, longitud__lte=180),
                name='ck_registro_longitud',
            ),
            models.CheckConstraint(
                condition=models.Q(estado_calidad__in=['PENDIENTE', 'VALIDADO', 'CORREGIDO']),
                name='ck_registro_estado',
            ),
        ]
        # Para sumar cuando exista codigo_externo:
        # models.UniqueConstraint(fields=['fuente', 'codigo_externo'], name='uq_registro_fuente_codigo')

    def __str__(self):
        especie = self.especie.nombre_cientifico if self.especie_id else 'Especie sin identificar'
        return f'{especie} · {self.fecha_avistamiento:%d/%m/%Y}'

    def clean(self):
        super().clean()
        errores = {}

        if self.fecha_avistamiento and self.fecha_avistamiento > timezone.localdate():
            errores['fecha_avistamiento'] = 'La fecha de avistamiento no puede ser futura.'

        # Un registro no puede estar validado/corregido si no tiene especie.
        if (
            self.estado_calidad in (self.EstadoCalidad.VALIDADO, self.EstadoCalidad.CORREGIDO)
            and not self.especie_id
        ):
            errores['especie'] = 'Un registro validado o corregido debe tener una especie asignada.'

        self.zona = _limpiar_espacios(self.zona)

        if errores:
            raise ValidationError(errores)

# =============================================================================
# Imagen (fotos de un avistamiento)
# =============================================================================

TAMANO_MAX_IMAGEN_MB = 8


def ruta_imagen(instance, filename):
    """
    Ruta donde se guarda la foto: avistamientos/AAAA/MM/<uuid>.<ext>
    Nunca se conserva el nombre original (puede traer datos personales o caracteres raros).
    """
    extension = os.path.splitext(filename)[1].lower()
    return f'avistamientos/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{extension}'


def validar_tamano_imagen(archivo):
    if archivo.size > TAMANO_MAX_IMAGEN_MB * 1024 * 1024:
        raise ValidationError(f'La imagen no puede superar los {TAMANO_MAX_IMAGEN_MB} MB.')


class Imagen(models.Model):
    # Tabla "imagen": id_imagen, id_registro, url, descripcion
    #
    # En la base se guarda la RUTA del archivo (columna "url", VARCHAR(255)); el archivo
    # en sí vive en MEDIA_ROOT. Se llama "archivo" en Python para no confundirlo con
    # imagen.archivo.url (la dirección web que arma Django).

    id_imagen = models.AutoField(primary_key=True)

    # CASCADE a propósito: una foto no tiene sentido sin su avistamiento. Si se elimina el
    # registro, se eliminan sus fotos (y el archivo, ver señal más abajo).
    registro = models.ForeignKey(
        Registro,
        on_delete=models.CASCADE,
        db_column='id_registro',
        related_name='imagenes',
        verbose_name='avistamiento',
    )
    archivo = models.ImageField(
        'imagen',
        upload_to=ruta_imagen,
        max_length=255,
        db_column='url',
        validators=[
            FileExtensionValidator(['jpg', 'jpeg', 'png', 'webp']),
            validar_tamano_imagen,
        ],
    )
    descripcion = models.CharField('descripción', max_length=255, blank=True)

    # --- Campo sugerido (NO está en el MR actual): permite saber cuándo se subió cada foto.
    fecha_subida = models.DateTimeField('fecha de subida', auto_now_add=True)

    class Meta:
        db_table = 'imagen'
        verbose_name = 'imagen'
        verbose_name_plural = 'imágenes'
        ordering = ['id_imagen']

    def __str__(self):
        return f'Imagen {self.pk} · avistamiento {self.registro_id}'

    def clean(self):
        super().clean()
        self.descripcion = _limpiar_espacios(self.descripcion)


@receiver(post_delete, sender=Imagen)
def borrar_archivo_de_imagen(sender, instance, **kwargs):
    """Django no borra el archivo al borrar la fila: se hace acá para no dejar fotos huérfanas."""
    if instance.archivo:
        instance.archivo.delete(save=False)


# =============================================================================
# Predicción de IA (reconocimiento de especie por imagen)
# =============================================================================

class PrediccionIA(models.Model):
    # Tabla "prediccion_ia": id_prediccion, id_registro, id_especie_sugerida,
    #                        id_usuario_revisor, confianza, aceptada, fecha
    #
    # Cada fila es UNA especie candidata propuesta por el modelo para un avistamiento
    # (el modelo devuelve varias, ordenadas por confianza). El administrador revisa las
    # candidatas y elige: la decisión final queda en Registro.especie (RF09, RF10).
    #
    # Qué significa "aceptada":
    #   None  -> sin revisar todavía
    #   True  -> el administrador eligió esta candidata
    #   False -> el administrador revisó y eligió otra especie, o descartó todas
    # Regla de aplicación (no se puede forzar en Oracle con un índice parcial): por cada
    # avistamiento hay como máximo UNA candidata aceptada. La garantiza la vista/servicio.

    id_prediccion = models.AutoField(primary_key=True)

    registro = models.ForeignKey(
        Registro,
        on_delete=models.CASCADE,
        db_column='id_registro',
        related_name='predicciones',
        verbose_name='avistamiento',
    )
    # --- Campo sugerido (NO está en el MR actual): qué foto originó la sugerencia.
    # SET_NULL para conservar la estadística de aciertos aunque la foto se elimine.
    imagen = models.ForeignKey(
        Imagen,
        on_delete=models.SET_NULL,
        db_column='id_imagen',
        related_name='predicciones',
        null=True,
        blank=True,
        verbose_name='imagen analizada',
    )
    # PROTECT: no se puede borrar una especie que el modelo sugirió (historial de entrenamiento).
    especie_sugerida = models.ForeignKey(
        Especie,
        on_delete=models.PROTECT,
        db_column='id_especie_sugerida',
        related_name='predicciones',
        verbose_name='especie sugerida',
    )
    # Nullable: mientras nadie revisó, no hay revisor.
    usuario_revisor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        db_column='id_usuario_revisor',
        related_name='predicciones_revisadas',
        null=True,
        blank=True,
        verbose_name='revisor',
    )

    # Probabilidad entre 0 y 1 (0.7850 = 78,5 %). Se muestra siempre junto a la sugerencia (RNF06).
    confianza = models.DecimalField(
        'confianza',
        max_digits=5,
        decimal_places=4,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('1'))],
    )
    aceptada = models.BooleanField('aceptada', null=True, blank=True)
    fecha = models.DateTimeField('fecha de la sugerencia', auto_now_add=True)

    # --- Campos sugeridos (NO están en el MR actual) ---
    # Posición en el ranking del modelo para ese avistamiento (1 = la más probable).
    posicion = models.PositiveSmallIntegerField('posición en el ranking', default=1)
    # Versión del modelo que generó la sugerencia, para comparar versiones más adelante.
    modelo_version = models.CharField('versión del modelo', max_length=50, blank=True)
    # RNF10: la corrección debe quedar con fecha y usuario responsable.
    fecha_revision = models.DateTimeField('fecha de revisión', null=True, blank=True)

    class Meta:
        db_table = 'prediccion_ia'
        verbose_name = 'predicción de IA'
        verbose_name_plural = 'predicciones de IA'
        # Orden determinista: las candidatas de cada avistamiento salen de mejor a peor.
        ordering = ['registro_id', 'posicion', 'id_prediccion']
        indexes = [
            models.Index(fields=['aceptada'], name='ix_prediccion_aceptada'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(confianza__gte=0, confianza__lte=1),
                name='ck_prediccion_confianza',
            ),
            models.CheckConstraint(
                condition=models.Q(posicion__gte=1),
                name='ck_prediccion_posicion',
            ),
            # No puede haber revisor sin decisión (aceptada tiene que ser True o False).
            models.CheckConstraint(
                condition=models.Q(aceptada__isnull=False) | models.Q(usuario_revisor__isnull=True),
                name='ck_prediccion_revisor',
            ),
        ]

    def __str__(self):
        return f'{self.especie_sugerida.nombre_cientifico} ({self.confianza:.0%}) · avistamiento {self.registro_id}'

    @property
    def estado(self):
        if self.aceptada is None:
            return 'pendiente'
        return 'aceptada' if self.aceptada else 'rechazada'

    @property
    def confianza_porcentaje(self):
        """78.5 para mostrar como '78,5 %' en las plantillas."""
        return (self.confianza * 100).quantize(Decimal('0.1'))

    def clean(self):
        super().clean()
        errores = {}

        # La imagen analizada tiene que ser del mismo avistamiento.
        if self.imagen_id and self.registro_id and self.imagen.registro_id != self.registro_id:
            errores['imagen'] = 'La imagen no pertenece a este avistamiento.'

        if self.aceptada is None and self.usuario_revisor_id:
            errores['aceptada'] = 'Una predicción con revisor debe estar aceptada o rechazada.'

        if errores:
            raise ValidationError(errores)