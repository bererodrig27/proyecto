from django.contrib.auth.models import AbstractUser
from django.db import models


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


class Fuente(models.Model):
    # Tabla "fuente": id_fuente (PK), nombre, tipo, url, confiabilidad
    id_fuente = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    tipo = models.CharField(max_length=50)
    url = models.URLField(max_length=255, blank=True)
    confiabilidad = models.CharField(max_length=50)

    class Meta:
        db_table = 'fuente'

    def __str__(self):
        return self.nombre


class Especie(models.Model):
    # Tabla "especie": id_especie (PK), nombre_cientifico, nombre_comun,
    # clase_taxonomica, taxonomia, estado_conservacion
    id_especie = models.AutoField(primary_key=True)
    nombre_cientifico = models.CharField(max_length=150, unique=True)
    nombre_comun = models.CharField(max_length=150, blank=True)
    clase_taxonomica = models.CharField(max_length=50)
    taxonomia = models.CharField(max_length=255, blank=True)
    estado_conservacion = models.CharField(max_length=50, blank=True)

    class Meta:
        db_table = 'especie'
        verbose_name_plural = 'especies'

    def __str__(self):
        return self.nombre_cientifico


class Registro(models.Model):
    # Tabla "registro": id_registro (PK), id_especie (FK), id_fuente (FK),
    # id_usuario_curador (FK), fecha_avistamiento, latitud, longitud, zona,
    # estado_calidad, fecha_carga

    class EstadoCalidad(models.TextChoices):
        PENDIENTE = 'PENDIENTE', 'Pendiente'
        VALIDADO = 'VALIDADO', 'Validado'
        CORREGIDO = 'CORREGIDO', 'Corregido'

    id_registro = models.AutoField(primary_key=True)
    especie = models.ForeignKey(
        Especie, on_delete=models.PROTECT,
        db_column='id_especie', related_name='registros',
        null=True, blank=True,  # nulo mientras la IA aún no sugirió/confirmó especie (RF09)
    )
    fuente = models.ForeignKey(
        Fuente, on_delete=models.PROTECT,
        db_column='id_fuente', related_name='registros',
    )
    usuario_curador = models.ForeignKey(
        Usuario, on_delete=models.SET_NULL,
        db_column='id_usuario_curador', related_name='registros_curados',
        null=True, blank=True,
    )
    fecha_avistamiento = models.DateField()
    latitud = models.DecimalField(max_digits=9, decimal_places=6)
    longitud = models.DecimalField(max_digits=9, decimal_places=6)
    zona = models.CharField(max_length=100)
    estado_calidad = models.CharField(
        max_length=30, choices=EstadoCalidad.choices,
        default=EstadoCalidad.PENDIENTE,
    )
    fecha_carga = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'registro'

    def __str__(self):
        return f"Registro #{self.id_registro} — {self.especie or 'sin especie'}"


class Imagen(models.Model):
    # Tabla "imagen": id_imagen (PK), id_registro (FK), url, descripcion
    id_imagen = models.AutoField(primary_key=True)
    registro = models.ForeignKey(
        Registro, on_delete=models.CASCADE,
        db_column='id_registro', related_name='imagenes',
    )
    url = models.URLField(max_length=255)
    descripcion = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = 'imagen'

    def __str__(self):
        return f"Imagen de registro #{self.registro_id}"