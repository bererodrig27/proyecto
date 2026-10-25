from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.utils import timezone
from django.contrib.auth.models import AbstractUser, UserManager  # sumá UserManager al import existente



# =============================================================================
# Usuarios y roles
# =============================================================================

class Rol(models.Model):
    ADMINISTRADOR = 'Administrador'
    VISITANTE = 'Visitante'
    NOMBRES_VALIDOS = (
        (ADMINISTRADOR, 'Administrador'),
        (VISITANTE, 'Visitante'),
    )

    id_rol = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=50, unique=True, choices=NOMBRES_VALIDOS)

    class Meta:
        db_table = 'rol'

    def __str__(self):
        return self.nombre

    @classmethod
    def administrador(cls):
        return cls.objects.get(nombre=cls.ADMINISTRADOR)

    @classmethod
    def visitante(cls):
        return cls.objects.get(nombre=cls.VISITANTE)

class UsuarioManager(UserManager):
    """Al crear un superusuario por consola, le asigna el rol Administrador."""

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields['rol'] = Rol.objects.get_or_create(nombre=Rol.ADMINISTRADOR)[0]
        return super().create_superuser(username, email, password, **extra_fields)


class Usuario(AbstractUser):
    # AbstractUser ya aporta: nombre (first_name/last_name), contraseña (hash),
    # activo (is_active). Se mantiene la tabla "rol" como FK (id_rol).
    email = models.EmailField(unique=True, blank=False)
    rol = models.ForeignKey(
        Rol,
        on_delete=models.PROTECT,
        db_column='id_rol',
        related_name='usuarios',
    )
    fecha_alta = models.DateTimeField(auto_now_add=True)

    objects = UsuarioManager()

    class Meta:
        db_table = 'usuario'

    def es_administrador(self):
        return self.rol.nombre == Rol.ADMINISTRADOR

    def es_visitante(self):
        return self.rol.nombre == Rol.VISITANTE


# =============================================================================
# Utilidades de normalización (evitar duplicados e inconsistencias)
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
# Fuente (medio de comunicación o portal desde el que se hace scraping)
# =============================================================================

class Fuente(models.Model):
    # Tabla "fuente": id_fuente, nombre, url_base, activa
    id_fuente = models.AutoField(primary_key=True)
    nombre = models.CharField('nombre', max_length=150, unique=True, help_text='Medio o portal.')
    url_base = models.URLField('URL base', max_length=255, blank=True)
    activa = models.BooleanField('activa', default=True)

    class Meta:
        db_table = 'fuente'
        verbose_name = 'fuente'
        verbose_name_plural = 'fuentes'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


# =============================================================================
# Noticia (nota periodística de la que sale uno o más avistamientos)
# =============================================================================

class Noticia(models.Model):
    # Tabla "noticia": id_noticia, id_fuente, url (UK), titulo, fecha_publicacion,
    #                  fecha_scraping, extracto, origen, estado_procesamiento
    #
    # La URL única evita procesar dos veces la misma nota. Solo se guarda un
    # extracto del texto, nunca la nota completa.

    class Origen(models.TextChoices):
        SCRAPING = 'SCRAPING', 'Scraping'
        MANUAL = 'MANUAL', 'Carga manual'

    class EstadoProcesamiento(models.TextChoices):
        PENDIENTE = 'PENDIENTE', 'Pendiente'
        PROCESADA = 'PROCESADA', 'Procesada'
        DESCARTADA = 'DESCARTADA', 'Descartada'
        ERROR = 'ERROR', 'Error'

    id_noticia = models.AutoField(primary_key=True)
    fuente = models.ForeignKey(
        Fuente,
        on_delete=models.PROTECT,
        db_column='id_fuente',
        related_name='noticias',
        verbose_name='fuente',
    )
    url = models.URLField('URL', max_length=500, unique=True)
    titulo = models.CharField('título', max_length=300)
    fecha_publicacion = models.DateField('fecha de publicación', null=True, blank=True)
    fecha_scraping = models.DateTimeField('fecha de scraping / carga', auto_now_add=True)
    extracto = models.TextField('extracto', blank=True)
    origen = models.CharField('origen', max_length=20, choices=Origen.choices, default=Origen.SCRAPING)
    estado_procesamiento = models.CharField(
        'estado de procesamiento',
        max_length=20,
        choices=EstadoProcesamiento.choices,
        default=EstadoProcesamiento.PENDIENTE,
    )

    class Meta:
        db_table = 'noticia'
        verbose_name = 'noticia'
        verbose_name_plural = 'noticias'
        ordering = [models.F('fecha_publicacion').desc(nulls_last=True), '-id_noticia']
        indexes = [
            models.Index(fields=['estado_procesamiento'], name='ix_noticia_estado'),
            models.Index(fields=['fecha_publicacion'], name='ix_noticia_fecha'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(origen__in=['SCRAPING', 'MANUAL']),
                name='ck_noticia_origen',
            ),
            models.CheckConstraint(
                condition=models.Q(estado_procesamiento__in=['PENDIENTE', 'PROCESADA', 'DESCARTADA', 'ERROR']),
                name='ck_noticia_estado',
            ),
        ]

    def __str__(self):
        return self.titulo

    def clean(self):
        super().clean()
        self.titulo = _limpiar_espacios(self.titulo)


# =============================================================================
# Especie
# =============================================================================

class Especie(models.Model):
    # Tabla "especie": id_especie, nombre_cientifico (UK), nombre_comun,
    #                  clase_taxonomica, estado_conservacion, descripcion
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
        max_length=100,
        blank=True,
        help_text='Por ejemplo: Aves, Mammalia, Reptilia, Amphibia.',
    )
    # Campo del proyecto original que NO figura en el MR del documento. Se conserva
    # para no perder datos ni romper formularios; se puede quitar si no se usa.
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
    descripcion = models.TextField('descripción', blank=True)

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
# Ubicación (lugar del avistamiento)
# =============================================================================

class Ubicacion(models.Model):
    # Tabla "ubicacion": id_ubicacion, nombre_lugar, zona, latitud, longitud, precision
    #
    # Las coordenadas son aproximadas: salen de geocodificar el lugar que
    # menciona la noticia. "precision" indica qué tan confiable es el punto.

    class Precision(models.TextChoices):
        EXACTA = 'EXACTA', 'Exacta'
        APROXIMADA = 'APROXIMADA', 'Aproximada'
        OFUSCADA = 'OFUSCADA', 'Ofuscada'

    id_ubicacion = models.AutoField(primary_key=True)
    nombre_lugar = models.CharField('lugar', max_length=200)
    zona = models.CharField('zona', max_length=100, blank=True)
    latitud = models.FloatField('latitud', validators=[MinValueValidator(-90), MaxValueValidator(90)])
    longitud = models.FloatField('longitud', validators=[MinValueValidator(-180), MaxValueValidator(180)])
    precision = models.CharField(
        'precisión',
        max_length=20,
        choices=Precision.choices,
        default=Precision.APROXIMADA,
    )

    class Meta:
        db_table = 'ubicacion'
        verbose_name = 'ubicación'
        verbose_name_plural = 'ubicaciones'
        ordering = ['nombre_lugar', 'id_ubicacion']
        indexes = [
            models.Index(fields=['zona'], name='ix_ubicacion_zona'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(latitud__gte=-90, latitud__lte=90),
                name='ck_ubicacion_latitud',
            ),
            models.CheckConstraint(
                condition=models.Q(longitud__gte=-180, longitud__lte=180),
                name='ck_ubicacion_longitud',
            ),
            models.CheckConstraint(
                condition=models.Q(precision__in=['EXACTA', 'APROXIMADA', 'OFUSCADA']),
                name='ck_ubicacion_precision',
            ),
        ]

    def __str__(self):
        return self.nombre_lugar

    def clean(self):
        super().clean()
        self.nombre_lugar = _limpiar_espacios(self.nombre_lugar)
        self.zona = _limpiar_espacios(self.zona)


# =============================================================================
# Registro (avistamiento respaldado por una noticia)
# =============================================================================

class Registro(models.Model):
    # Tabla "registro": id_registro, id_noticia, id_especie, id_ubicacion,
    #                   fecha_avistamiento, tipo_registro, estado_calidad,
    #                   fragmento_origen, id_usuario_carga, id_usuario_validador,
    #                   fecha_validacion
    #
    # En la interfaz se muestra como "Avistamiento" (verbose_name).

    class EstadoCalidad(models.TextChoices):
        PENDIENTE = 'PENDIENTE', 'Pendiente'
        VALIDADO = 'VALIDADO', 'Validado'
        CORREGIDO = 'CORREGIDO', 'Corregido'
        DESCARTADO = 'DESCARTADO', 'Descartado'

    id_registro = models.AutoField(primary_key=True)

    # Obligatoria: todo registro (también los cargados a mano) apunta a una noticia.
    noticia = models.ForeignKey(
        Noticia,
        on_delete=models.PROTECT,
        db_column='id_noticia',
        related_name='registros',
        verbose_name='noticia',
    )
    # Nulo hasta que se resuelva la especie (la noticia puede mencionar una que no
    # está en el catálogo).
    especie = models.ForeignKey(
        Especie,
        on_delete=models.PROTECT,
        db_column='id_especie',
        related_name='registros',
        null=True,
        blank=True,
        verbose_name='especie',
    )
    # Nulo si no se pudo ubicar el lugar.
    ubicacion = models.ForeignKey(
        Ubicacion,
        on_delete=models.PROTECT,
        db_column='id_ubicacion',
        related_name='registros',
        null=True,
        blank=True,
        verbose_name='ubicación',
    )

    # Nula si la noticia no precisa la fecha.
    fecha_avistamiento = models.DateField('fecha de avistamiento', null=True, blank=True)
    tipo_registro = models.CharField(
        'tipo de registro',
        max_length=50,
        blank=True,
        help_text='Por ejemplo: avistamiento directo, rastro, hallazgo, cámara trampa.',
    )
    estado_calidad = models.CharField(
        'estado de calidad',
        max_length=20,
        choices=EstadoCalidad.choices,
        default=EstadoCalidad.PENDIENTE,
    )
    # Fragmento de la noticia que respalda el registro (procedencia verificable).
    fragmento_origen = models.TextField('fragmento de origen', blank=True)

    # Nulo si el registro viene del scraping.
    usuario_carga = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        db_column='id_usuario_carga',
        related_name='registros_cargados',
        null=True,
        blank=True,
        verbose_name='cargado por',
    )
    # Nulo hasta la revisión.
    usuario_validador = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        db_column='id_usuario_validador',
        related_name='registros_validados',
        null=True,
        blank=True,
        verbose_name='validado por',
    )
    fecha_validacion = models.DateTimeField('fecha de validación', null=True, blank=True)

    class Meta:
        db_table = 'registro'
        verbose_name = 'avistamiento'
        verbose_name_plural = 'avistamientos'
        # Orden determinista (el id desempata) y con fechas nulas al final:
        # necesario para paginar bien en Oracle.
        ordering = [models.F('fecha_avistamiento').desc(nulls_last=True), '-id_registro']
        indexes = [
            models.Index(fields=['fecha_avistamiento'], name='ix_registro_fecha'),
            models.Index(fields=['estado_calidad'], name='ix_registro_calidad'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(estado_calidad__in=['PENDIENTE', 'VALIDADO', 'CORREGIDO', 'DESCARTADO']),
                name='ck_registro_estado',
            ),
        ]

    def __str__(self):
        especie = self.especie.nombre_cientifico if self.especie_id else 'Especie sin identificar'
        if self.fecha_avistamiento:
            return f'{especie} · {self.fecha_avistamiento:%d/%m/%Y}'
        return f'{especie} · sin fecha'

    # --- Accesos de solo lectura para que las plantillas sigan funcionando ---
    # (r.zona, r.latitud, r.longitud, r.fuente y el link a la noticia).
    # Para filtrar/ordenar en consultas hay que usar la ruta real:
    # ubicacion__zona, noticia__fuente, etc.

    @property
    def url_noticia(self):
        return self.noticia.url

    @property
    def fuente(self):
        return self.noticia.fuente

    @property
    def zona(self):
        return self.ubicacion.zona if self.ubicacion_id else ''

    @property
    def latitud(self):
        return self.ubicacion.latitud if self.ubicacion_id else None

    @property
    def longitud(self):
        return self.ubicacion.longitud if self.ubicacion_id else None

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

        self.tipo_registro = _limpiar_espacios(self.tipo_registro)

        if errores:
            raise ValidationError(errores)


# =============================================================================
# Extracción de IA (resultado del modelo de lenguaje y su revisión)
# =============================================================================

class ExtraccionIA(models.Model):
    # Tabla "extraccion_ia": id_extraccion, id_registro (FK, UK), modelo,
    #                        version_prompt, json_extraido, confianza,
    #                        estado_revision, json_corregido, id_usuario_revisor,
    #                        fecha_extraccion, fecha_revision
    #
    # Relación 1 a 1 con el registro: una noticia puede generar varios avistamientos,
    # pero cada uno tiene su propia extracción. Los registros cargados a mano por un
    # Administrador no tienen extracción.

    class EstadoRevision(models.TextChoices):
        PENDIENTE = 'PENDIENTE', 'Pendiente'
        ACEPTADA = 'ACEPTADA', 'Aceptada'
        CORREGIDA = 'CORREGIDA', 'Corregida'
        RECHAZADA = 'RECHAZADA', 'Rechazada'

    # Cómo se traduce la revisión al estado del registro.
    ESTADO_REGISTRO = {
        EstadoRevision.ACEPTADA: Registro.EstadoCalidad.VALIDADO,
        EstadoRevision.CORREGIDA: Registro.EstadoCalidad.CORREGIDO,
        EstadoRevision.RECHAZADA: Registro.EstadoCalidad.DESCARTADO,
    }

    id_extraccion = models.AutoField(primary_key=True)
    registro = models.OneToOneField(
        Registro,
        on_delete=models.CASCADE,
        db_column='id_registro',
        related_name='extraccion',
        verbose_name='avistamiento',
    )
    modelo = models.CharField('modelo', max_length=100)
    version_prompt = models.CharField('versión del prompt', max_length=20, blank=True)
    json_extraido = models.JSONField('JSON extraído')
    # Calculada por el sistema a partir de controles de calidad, NO autodeclarada
    # por el modelo de lenguaje. Valor entre 0 y 1 (0.7850 = 78,5 %).
    confianza = models.DecimalField(
        'confianza',
        max_digits=5,
        decimal_places=4,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('1'))],
    )
    estado_revision = models.CharField(
        'estado de revisión',
        max_length=20,
        choices=EstadoRevision.choices,
        default=EstadoRevision.PENDIENTE,
    )
    # Nulo si no se corrigió.
    json_corregido = models.JSONField('JSON corregido', null=True, blank=True)
    # Nulo hasta la revisión.
    usuario_revisor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        db_column='id_usuario_revisor',
        related_name='extracciones_revisadas',
        null=True,
        blank=True,
        verbose_name='revisor',
    )
    fecha_extraccion = models.DateTimeField('fecha de extracción', auto_now_add=True)
    fecha_revision = models.DateTimeField('fecha de revisión', null=True, blank=True)

    class Meta:
        db_table = 'extraccion_ia'
        verbose_name = 'extracción de IA'
        verbose_name_plural = 'extracciones de IA'
        ordering = ['-fecha_extraccion', '-id_extraccion']
        indexes = [
            models.Index(fields=['estado_revision'], name='ix_extraccion_estado'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(confianza__gte=0, confianza__lte=1),
                name='ck_extraccion_confianza',
            ),
            models.CheckConstraint(
                condition=models.Q(estado_revision__in=['PENDIENTE', 'ACEPTADA', 'CORREGIDA', 'RECHAZADA']),
                name='ck_extraccion_estado',
            ),
            # No puede haber una revisión resuelta sin revisor.
            models.CheckConstraint(
                condition=models.Q(estado_revision='PENDIENTE') | models.Q(usuario_revisor__isnull=False),
                name='ck_extraccion_revisor',
            ),
        ]

    def __str__(self):
        return f'Extracción {self.pk} · avistamiento {self.registro_id} ({self.get_estado_revision_display()})'

    @property
    def confianza_porcentaje(self):
        """78.5 para mostrar como '78,5 %' en las plantillas."""
        return (self.confianza * 100).quantize(Decimal('0.1'))

    def clean(self):
        super().clean()
        errores = {}

        if self.estado_revision == self.EstadoRevision.CORREGIDA and not self.json_corregido:
            errores['json_corregido'] = 'Una extracción corregida debe incluir el JSON corregido.'
        if self.estado_revision == self.EstadoRevision.PENDIENTE and self.usuario_revisor_id:
            errores['estado_revision'] = 'Una extracción con revisor no puede estar pendiente.'

        if errores:
            raise ValidationError(errores)

    @transaction.atomic
    def revisar(self, usuario, estado, json_corregido=None):
        """
        Registra la revisión del Administrador y traslada el resultado al registro:
        ACEPTADA -> VALIDADO, CORREGIDA -> CORREGIDO, RECHAZADA -> DESCARTADO.
        Si algo falla, no se guarda nada (transacción).
        """
        if estado not in self.ESTADO_REGISTRO:
            raise ValueError('El estado debe ser ACEPTADA, CORREGIDA o RECHAZADA.')

        ahora = timezone.now()
        self.estado_revision = estado
        self.json_corregido = json_corregido if estado == self.EstadoRevision.CORREGIDA else None
        self.usuario_revisor = usuario
        self.fecha_revision = ahora
        self.full_clean()
        self.save()

        registro = self.registro
        registro.estado_calidad = self.ESTADO_REGISTRO[estado]
        registro.usuario_validador = usuario
        registro.fecha_validacion = ahora
        registro.full_clean()
        registro.save()


# =============================================================================
# Auditoría (la llenan triggers de la base de datos, no la aplicación)
# =============================================================================

class Auditoria(models.Model):
    # Tabla "auditoria": id_auditoria, tabla_afectada, id_registro_afectado,
    #                    operacion, valores_anteriores, valores_nuevos,
    #                    usuario_bd, fecha_hora
    #
    # Se modela SIN claves foráneas a propósito: es una tabla genérica, poblada por
    # triggers AFTER INSERT/UPDATE/DELETE sobre registro, especie, usuario y
    # extraccion_ia. Los triggers se crean en una migración (RunSQL). Desde la
    # aplicación solo se consulta.

    class Operacion(models.TextChoices):
        INSERT = 'INSERT', 'INSERT'
        UPDATE = 'UPDATE', 'UPDATE'
        DELETE = 'DELETE', 'DELETE'

    id_auditoria = models.AutoField(primary_key=True)
    tabla_afectada = models.CharField('tabla afectada', max_length=50)
    id_registro_afectado = models.IntegerField('id del registro afectado')
    operacion = models.CharField('operación', max_length=10, choices=Operacion.choices)
    valores_anteriores = models.JSONField('valores anteriores', null=True, blank=True)
    valores_nuevos = models.JSONField('valores nuevos', null=True, blank=True)
    # Usuario de conexión a nivel de base de datos (auditoría de infraestructura).
    usuario_bd = models.CharField('usuario de BD', max_length=100)
    fecha_hora = models.DateTimeField('fecha y hora', default=timezone.now)

    class Meta:
        db_table = 'auditoria'
        verbose_name = 'registro de auditoría'
        verbose_name_plural = 'auditoría'
        ordering = ['-fecha_hora', '-id_auditoria']
        indexes = [
            models.Index(fields=['tabla_afectada', 'id_registro_afectado'], name='ix_auditoria_tabla_id'),
        ]

    def __str__(self):
        return f'{self.operacion} en {self.tabla_afectada} #{self.id_registro_afectado}'


