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