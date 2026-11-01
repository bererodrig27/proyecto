"""
Configuración para correr los tests sin Oracle.

Usa SQLite en memoria: es instantáneo y no necesita privilegios de base de datos
(crear la base de pruebas en Oracle exige CREATE USER, que el usuario "django" no tiene).

    python manage.py test MisionaBio --settings=config.settings_test

Los triggers de auditoría son PL/SQL y solo existen en Oracle: en SQLite la migración
0005 se omite y los tests que dependen de ellos se saltean solos.
"""
from .settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

# Hash de contraseñas rápido: acelera los tests que crean usuarios.
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']