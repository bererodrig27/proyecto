"""
Pruebas unitarias de MisionaBio y del módulo de scraping.

Cómo correrlas (sin Oracle, con SQLite en memoria):

    python manage.py test MisionaBio --settings=config.settings_test

Con cobertura:

    coverage run manage.py test MisionaBio --settings=config.settings_test
    coverage report

Notas:
  * Ninguna prueba toca la red ni la API de Gemini: todo lo externo (requests, Nominatim,
    Gemini, time.sleep) se reemplaza con mocks.
  * Los triggers de auditoría son PL/SQL y solo existen en Oracle. Las pruebas del SQL se
    hacen como texto y la prueba que dispara un trigger real se saltea si el motor no es Oracle.

Contenido:
  1. Modelos
  2. Migración de triggers de auditoría
  3. Formularios
  4. Vistas (permisos, CRUD, filtros, dashboard)
  5. Admin
  6. Scraping: normalizar, confianza, extraer_ia, descargar, descubrir, geocodificar, guardar
"""
import contextlib
import importlib
import io
import json
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest import expectedFailure, mock, skipUnless

import requests
from django.contrib import admin as django_admin
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from scraping import config, confianza, descargar, descubrir, extraer_ia, geocodificar, guardar, normalizar

from .admin import AuditoriaAdmin
from .forms import (
    AvistamientoFiltroForm,
    AvistamientoForm,
    EspecieFiltroForm,
    EspecieForm,
    RegistroAdminForm,
    RegistroUsuarioForm,
)
from .models import (
    Auditoria,
    Especie,
    ExtraccionIA,
    Fuente,
    Noticia,
    Registro,
    Rol,
    Ubicacion,
    Usuario,
    _limpiar_espacios,
    normalizar_nombre_cientifico,
)

PASSWORD = 'Clave-Segura-2026'


# =============================================================================
# Utilidades para armar datos de prueba
# =============================================================================

@contextlib.contextmanager
def silenciar():
    """Los módulos de scraping imprimen por consola; se oculta para no ensuciar la salida."""
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def crear_admin(username='admin'):
    rol, _ = Rol.objects.get_or_create(nombre=Rol.ADMINISTRADOR)
    return Usuario.objects.create_user(username, f'{username}@test.com', PASSWORD, rol=rol)


def crear_visitante(username='visitante'):
    rol, _ = Rol.objects.get_or_create(nombre=Rol.VISITANTE)
    return Usuario.objects.create_user(username, f'{username}@test.com', PASSWORD, rol=rol)


def crear_fuente(nombre='Medio de prueba', **extra):
    return Fuente.objects.create(nombre=nombre, url_base='https://medio.test', **extra)


def crear_noticia(fuente=None, url='https://medio.test/nota-1', **extra):
    datos = {'titulo': 'Avistan un yaguareté en Iguazú', 'fecha_publicacion': date(2026, 5, 10)}
    datos.update(extra)
    if fuente is None:
        # Reutiliza la misma fuente por defecto para poder crear varias noticias
        fuente = Fuente.objects.get_or_create(
            nombre='Medio de prueba', defaults={'url_base': 'https://medio.test'})[0]
    return Noticia.objects.create(fuente=fuente, url=url, **datos)


def crear_especie(cientifico='Panthera onca', comun='Yaguareté', **extra):
    datos = {'clase_taxonomica': 'Mammalia'}
    datos.update(extra)
    return Especie.objects.create(nombre_cientifico=cientifico, nombre_comun=comun, **datos)


def crear_ubicacion(**extra):
    datos = {'nombre_lugar': 'Puerto Iguazú', 'zona': 'Selva paranaense', 'latitud': -25.6, 'longitud': -54.57}
    datos.update(extra)
    return Ubicacion.objects.create(**datos)


def crear_registro(noticia=None, **extra):
    """Registro de scraping (origen por defecto) con su noticia."""
    datos = {
        'noticia': noticia or crear_noticia(url=f'https://medio.test/n-{Noticia.objects.count() + 1}'),
        'fecha_avistamiento': date(2026, 5, 9),
        'tipo_registro': 'avistamiento directo',
    }
    datos.update(extra)
    return Registro.objects.create(**datos)


def crear_registro_manual(usuario, **extra):
    datos = {
        'origen_registro': Registro.OrigenRegistro.MANUAL,
        'usuario_carga': usuario,
        'observaciones': 'Lo vio un guardaparque durante un recorrido.',
        'fecha_avistamiento': date(2026, 5, 9),
    }
    datos.update(extra)
    return Registro.objects.create(**datos)


# =============================================================================
# 1. MODELOS
# =============================================================================

class UtilidadesDeTextoTests(SimpleTestCase):
    def test_limpiar_espacios_colapsa_y_recorta(self):
        self.assertEqual(_limpiar_espacios('  hola    mundo  '), 'hola mundo')

    def test_limpiar_espacios_acepta_none(self):
        self.assertEqual(_limpiar_espacios(None), '')

    def test_nombre_cientifico_formato_binomial(self):
        self.assertEqual(normalizar_nombre_cientifico('panthera   ONCA'), 'Panthera onca')

    def test_nombre_cientifico_vacio(self):
        self.assertEqual(normalizar_nombre_cientifico('   '), '')

    def test_nombre_cientifico_de_un_solo_termino(self):
        self.assertEqual(normalizar_nombre_cientifico('PANTHERA'), 'Panthera')


class RolYUsuarioTests(TestCase):
    def test_rol_visitante_classmethod(self):
        Rol.objects.create(nombre=Rol.VISITANTE)
        self.assertEqual(Rol.visitante().nombre, Rol.VISITANTE)

    def test_rol_str(self):
        rol = Rol.objects.create(nombre=Rol.ADMINISTRADOR)
        self.assertIn('Administrador', str(rol))

    def test_roles_del_usuario(self):
        admin, visitante = crear_admin(), crear_visitante()
        self.assertTrue(admin.es_administrador())
        self.assertFalse(admin.es_visitante())
        self.assertTrue(visitante.es_visitante())
        self.assertFalse(visitante.es_administrador())

    def test_superusuario_recibe_rol_administrador(self):
        root = Usuario.objects.create_superuser('root', 'root@test.com', PASSWORD)
        self.assertEqual(root.rol.nombre, Rol.ADMINISTRADOR)
        self.assertTrue(root.es_administrador())


class FuenteYNoticiaModelTests(TestCase):
    def test_fuente_str(self):
        self.assertEqual(str(crear_fuente('Diario Misionero')), 'Diario Misionero')

    def test_nombre_de_fuente_es_unico(self):
        crear_fuente('Medio')
        with self.assertRaises(IntegrityError), transaction.atomic():
            crear_fuente('Medio')

    def test_noticia_str_y_valores_por_defecto(self):
        noticia = crear_noticia()
        self.assertEqual(str(noticia), 'Avistan un yaguareté en Iguazú')
        self.assertEqual(noticia.origen, Noticia.Origen.SCRAPING)
        self.assertEqual(noticia.estado_procesamiento, Noticia.EstadoProcesamiento.PENDIENTE)
        self.assertIsNotNone(noticia.fecha_scraping)

    def test_noticia_clean_limpia_el_titulo(self):
        noticia = Noticia(fuente=crear_fuente(), url='https://medio.test/x', titulo='  Un   titulo  ')
        noticia.clean()
        self.assertEqual(noticia.titulo, 'Un titulo')

    def test_url_de_noticia_es_unica(self):
        fuente = crear_fuente()
        crear_noticia(fuente, 'https://medio.test/repetida')
        with self.assertRaises(IntegrityError), transaction.atomic():
            crear_noticia(fuente, 'https://medio.test/repetida')

    def test_estado_de_procesamiento_invalido_viola_constraint(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            crear_noticia(estado_procesamiento='INVENTADO')


class EspecieModelTests(TestCase):
    def test_str_con_y_sin_nombre_comun(self):
        self.assertEqual(str(crear_especie()), 'Panthera onca (Yaguareté)')
        self.assertEqual(str(crear_especie('Puma concolor', '')), 'Puma concolor')

    def test_save_normaliza_los_nombres(self):
        especie = Especie.objects.create(
            nombre_cientifico='  panthera   ONCA ', nombre_comun='  Yaguareté  ',
            clase_taxonomica='  MAMMALIA ', taxonomia=' Animalia   >  Chordata ',
        )
        self.assertEqual(especie.nombre_cientifico, 'Panthera onca')
        self.assertEqual(especie.nombre_comun, 'Yaguareté')
        self.assertEqual(especie.clase_taxonomica, 'Mammalia')
        self.assertEqual(especie.taxonomia, 'Animalia > Chordata')

    def test_valor_por_defecto_del_estado_de_conservacion(self):
        self.assertEqual(crear_especie().estado_conservacion, Especie.EstadoConservacion.NO_EVALUADA)

    def test_full_clean_detecta_duplicado_aunque_cambie_el_formato(self):
        crear_especie('Panthera onca')
        duplicada = Especie(nombre_cientifico='panthera ONCA')
        with self.assertRaises(ValidationError) as ctx:
            duplicada.full_clean()
        self.assertIn('nombre_cientifico', ctx.exception.message_dict)


class UbicacionModelTests(TestCase):
    def test_str(self):
        self.assertEqual(str(crear_ubicacion()), 'Puerto Iguazú')

    def test_clean_limpia_espacios(self):
        ub = Ubicacion(nombre_lugar='  Puerto   Iguazú ', zona=' Selva  ', departamento=' Iguazú ',
                       latitud=-25.6, longitud=-54.5)
        ub.clean()
        self.assertEqual((ub.nombre_lugar, ub.zona, ub.departamento), ('Puerto Iguazú', 'Selva', 'Iguazú'))

    def test_precision_por_defecto_es_aproximada(self):
        self.assertEqual(crear_ubicacion().precision, Ubicacion.Precision.APROXIMADA)

    def test_validadores_de_latitud_y_longitud(self):
        ub = Ubicacion(nombre_lugar='X', latitud=120, longitud=-200)
        with self.assertRaises(ValidationError) as ctx:
            ub.full_clean()
        self.assertEqual(set(ctx.exception.message_dict), {'latitud', 'longitud'})

    def test_constraint_de_latitud_en_la_base(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            crear_ubicacion(latitud=95)


class RegistroModelTests(TestCase):
    def test_str_con_especie_y_fecha(self):
        reg = crear_registro(especie=crear_especie())
        self.assertEqual(str(reg), 'Panthera onca · 09/05/2026')

    def test_str_sin_especie_ni_fecha(self):
        reg = crear_registro(fecha_avistamiento=None)
        self.assertEqual(str(reg), 'Especie sin identificar · sin fecha')

    def test_propiedades_con_noticia_y_ubicacion(self):
        noticia = crear_noticia()
        ub = crear_ubicacion()
        reg = crear_registro(noticia, ubicacion=ub)
        self.assertEqual(reg.url_noticia, noticia.url)
        self.assertEqual(reg.fuente, noticia.fuente)
        self.assertEqual(reg.zona, 'Selva paranaense')
        self.assertEqual((reg.latitud, reg.longitud), (-25.6, -54.57))
        self.assertFalse(reg.es_manual)

    def test_propiedades_sin_noticia_ni_ubicacion(self):
        reg = crear_registro_manual(crear_admin())
        self.assertTrue(reg.es_manual)
        self.assertIsNone(reg.url_noticia)
        self.assertIsNone(reg.fuente)
        self.assertEqual(reg.zona, '')
        self.assertIsNone(reg.latitud)
        self.assertIsNone(reg.longitud)

    def test_clean_rechaza_fecha_futura(self):
        reg = crear_registro(fecha_avistamiento=timezone.localdate() + timedelta(days=2))
        with self.assertRaises(ValidationError) as ctx:
            reg.clean()
        self.assertIn('fecha_avistamiento', ctx.exception.message_dict)

    def test_validado_o_corregido_exige_especie(self):
        for estado in (Registro.EstadoCalidad.VALIDADO, Registro.EstadoCalidad.CORREGIDO):
            reg = crear_registro(estado_calidad=estado)
            with self.assertRaises(ValidationError) as ctx:
                reg.clean()
            self.assertIn('especie', ctx.exception.message_dict)

    def test_validado_con_especie_es_valido(self):
        reg = crear_registro(especie=crear_especie(), estado_calidad=Registro.EstadoCalidad.VALIDADO)
        reg.clean()  # no debe lanzar

    def test_scraping_exige_noticia(self):
        reg = Registro(origen_registro=Registro.OrigenRegistro.SCRAPING)
        with self.assertRaises(ValidationError) as ctx:
            reg.clean()
        self.assertIn('noticia', ctx.exception.message_dict)

    def test_manual_exige_observaciones(self):
        reg = Registro(origen_registro=Registro.OrigenRegistro.MANUAL, observaciones='   ')
        with self.assertRaises(ValidationError) as ctx:
            reg.clean()
        self.assertIn('observaciones', ctx.exception.message_dict)

    def test_clean_limpia_tipo_de_registro(self):
        reg = crear_registro(tipo_registro='  cámara    trampa ')
        reg.clean()
        self.assertEqual(reg.tipo_registro, 'cámara trampa')

    def test_constraint_de_procedencia_scraping_sin_noticia(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Registro.objects.create(origen_registro=Registro.OrigenRegistro.SCRAPING)

    def test_constraint_de_procedencia_manual_sin_usuario(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Registro.objects.create(origen_registro=Registro.OrigenRegistro.MANUAL, observaciones='x')

    def test_especie_con_registros_esta_protegida(self):
        especie = crear_especie()
        crear_registro(especie=especie)
        from django.db.models import ProtectedError
        with self.assertRaises(ProtectedError):
            especie.delete()


class ExtraccionIAModelTests(TestCase):
    def setUp(self):
        self.admin = crear_admin()
        self.registro = crear_registro(especie=crear_especie())
        self.extraccion = ExtraccionIA.objects.create(
            registro=self.registro, modelo='gemini-test', json_extraido={'cita': 'x'},
            confianza=Decimal('0.7850'),
        )

    def test_str_y_porcentaje(self):
        self.assertIn('Pendiente', str(self.extraccion))
        self.assertEqual(self.extraccion.confianza_porcentaje, Decimal('78.5'))

    def test_clean_corregida_exige_json_corregido(self):
        self.extraccion.estado_revision = ExtraccionIA.EstadoRevision.CORREGIDA
        self.extraccion.usuario_revisor = self.admin
        with self.assertRaises(ValidationError) as ctx:
            self.extraccion.clean()
        self.assertIn('json_corregido', ctx.exception.message_dict)

    def test_clean_pendiente_con_revisor_es_invalido(self):
        self.extraccion.usuario_revisor = self.admin
        with self.assertRaises(ValidationError) as ctx:
            self.extraccion.clean()
        self.assertIn('estado_revision', ctx.exception.message_dict)

    def test_revisar_aceptada_valida_el_registro(self):
        self.extraccion.revisar(self.admin, ExtraccionIA.EstadoRevision.ACEPTADA)
        self.extraccion.refresh_from_db()
        self.registro.refresh_from_db()
        self.assertEqual(self.extraccion.estado_revision, 'ACEPTADA')
        self.assertEqual(self.extraccion.usuario_revisor, self.admin)
        self.assertIsNotNone(self.extraccion.fecha_revision)
        self.assertEqual(self.registro.estado_calidad, Registro.EstadoCalidad.VALIDADO)
        self.assertEqual(self.registro.usuario_validador, self.admin)
        self.assertIsNotNone(self.registro.fecha_validacion)

    def test_revisar_corregida_guarda_el_json_corregido(self):
        corregido = {'nombre_comun': 'Jaguar'}
        self.extraccion.revisar(self.admin, ExtraccionIA.EstadoRevision.CORREGIDA, corregido)
        self.extraccion.refresh_from_db()
        self.registro.refresh_from_db()
        self.assertEqual(self.extraccion.json_corregido, corregido)
        self.assertEqual(self.registro.estado_calidad, Registro.EstadoCalidad.CORREGIDO)

    def test_revisar_rechazada_descarta_el_registro(self):
        self.extraccion.revisar(self.admin, ExtraccionIA.EstadoRevision.RECHAZADA, {'ignorado': True})
        self.extraccion.refresh_from_db()
        self.registro.refresh_from_db()
        self.assertIsNone(self.extraccion.json_corregido)  # solo se guarda si es CORREGIDA
        self.assertEqual(self.registro.estado_calidad, Registro.EstadoCalidad.DESCARTADO)

    def test_revisar_estado_invalido(self):
        with self.assertRaises(ValueError):
            self.extraccion.revisar(self.admin, ExtraccionIA.EstadoRevision.PENDIENTE)

    def test_revisar_falla_sin_guardar_nada_si_el_registro_no_tiene_especie(self):
        sin_especie = crear_registro()
        extraccion = ExtraccionIA.objects.create(
            registro=sin_especie, modelo='m', json_extraido={'a': 1}, confianza=Decimal('0.5'),
        )
        with self.assertRaises(ValidationError):
            extraccion.revisar(self.admin, ExtraccionIA.EstadoRevision.ACEPTADA)
        extraccion.refresh_from_db()
        self.assertEqual(extraccion.estado_revision, ExtraccionIA.EstadoRevision.PENDIENTE)

    def test_al_borrar_el_registro_se_borra_su_extraccion(self):
        self.registro.delete()
        self.assertFalse(ExtraccionIA.objects.exists())

    def test_confianza_fuera_de_rango_viola_constraint(self):
        otro = crear_registro()
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExtraccionIA.objects.create(registro=otro, modelo='m', json_extraido={'a': 1},
                                        confianza=Decimal('1.5'))

    def test_revision_resuelta_sin_revisor_viola_constraint(self):
        otro = crear_registro()
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExtraccionIA.objects.create(registro=otro, modelo='m', json_extraido={'a': 1},
                                        confianza=Decimal('0.5'), estado_revision='ACEPTADA')


class AuditoriaModelTests(TestCase):
    def test_str(self):
        aud = Auditoria(tabla_afectada='registro', id_registro_afectado=7, operacion='DELETE',
                        usuario_bd='DJANGO')
        self.assertEqual(str(aud), 'DELETE en registro #7')

    def test_se_puede_guardar_con_json(self):
        aud = Auditoria.objects.create(
            tabla_afectada='especie', id_registro_afectado=1, operacion=Auditoria.Operacion.INSERT,
            valores_nuevos={'nombre_cientifico': 'Panthera onca'}, usuario_bd='DJANGO',
        )
        aud.refresh_from_db()
        self.assertEqual(aud.valores_nuevos['nombre_cientifico'], 'Panthera onca')
        self.assertIsNone(aud.valores_anteriores)


# =============================================================================
# 2. MIGRACIÓN DE TRIGGERS DE AUDITORÍA
# =============================================================================

class MigracionTriggersAuditoriaTests(SimpleTestCase):
    """El SQL es PL/SQL de Oracle: acá se valida su texto; el efecto real se prueba más abajo."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mig = importlib.import_module('MisionaBio.migrations.0005_triggers_auditoria')

    def test_hay_un_trigger_por_tabla_critica(self):
        self.assertEqual(set(self.mig.TABLAS), {'usuario', 'especie', 'registro', 'extraccion_ia'})
        self.assertEqual(len(self.mig.SQL_CREAR), 4)
        self.assertEqual(len(self.mig.SQL_BORRAR), 4)

    def test_cada_trigger_cubre_insert_update_delete(self):
        for tabla, sql in zip(self.mig.TABLAS, self.mig.SQL_CREAR):
            with self.subTest(tabla=tabla):
                self.assertIn(f'ON {tabla}', sql)
                self.assertIn('AFTER INSERT OR UPDATE OR DELETE', sql)
                self.assertIn("'INSERT'", sql)
                self.assertIn("'UPDATE'", sql)
                self.assertIn("'DELETE'", sql)
                self.assertIn('INSERT INTO auditoria', sql)

    def test_el_borrado_guarda_los_valores_anteriores(self):
        sql = self.mig.SQL_CREAR[2]  # registro
        rama_delete = sql.split("v_operacion := 'DELETE';")[1]
        self.assertIn(':OLD.id_registro', rama_delete)
        self.assertIn('INTO v_anterior', rama_delete)
        self.assertNotIn('INTO v_nuevo', rama_delete)

    def test_termina_en_end_punto_y_coma_con_salto_de_linea(self):
        # Django le quita un ";" o "/" final al SQL: sin el salto de línea se perdería el ";" de END;
        for sql in self.mig.SQL_CREAR:
            self.assertTrue(sql.endswith('END;\n'))

    def test_nunca_se_registra_el_hash_de_la_contrasena(self):
        sql = self.mig.SQL_CREAR[0]  # usuario
        self.assertIn('password_sha256', sql)
        self.assertIn('STANDARD_HASH', sql)
        self.assertNotIn("'password' VALUE", sql)
        self.assertNotIn('last_login', sql)

    def test_los_json_se_incrustan_como_json(self):
        sql = self.mig.SQL_CREAR[3]  # extraccion_ia
        self.assertIn('FORMAT JSON', sql)

    def test_solo_se_ejecuta_en_oracle(self):
        editor = mock.Mock()
        editor.connection.vendor = 'sqlite'
        self.mig.crear_triggers(None, editor)
        self.mig.borrar_triggers(None, editor)
        editor.execute.assert_not_called()

    def test_en_oracle_ejecuta_cada_sentencia(self):
        editor = mock.Mock()
        editor.connection.vendor = 'oracle'
        self.mig.crear_triggers(None, editor)
        self.assertEqual(editor.execute.call_count, 4)
        editor.execute.assert_any_call(self.mig.SQL_CREAR[0], params=None)
        editor.execute.reset_mock()
        self.mig.borrar_triggers(None, editor)
        self.assertEqual(editor.execute.call_count, 4)


@skipUnless(connection.vendor == 'oracle', 'Los triggers de auditoría solo existen en Oracle')
class TriggersAuditoriaEnOracleTests(TestCase):
    def test_borrar_un_registro_genera_auditoria_delete(self):
        reg = crear_registro()
        pk = reg.pk
        reg.delete()
        aud = Auditoria.objects.filter(tabla_afectada='registro', operacion='DELETE',
                                       id_registro_afectado=pk).first()
        self.assertIsNotNone(aud)
        self.assertIsNotNone(aud.valores_anteriores)
        self.assertIsNone(aud.valores_nuevos)

    def test_insert_y_update_de_especie(self):
        esp = crear_especie()
        esp.nombre_comun = 'Jaguar'
        esp.save()
        operaciones = list(Auditoria.objects.filter(tabla_afectada='especie').values_list('operacion', flat=True))
        self.assertIn('INSERT', operaciones)
        self.assertIn('UPDATE', operaciones)


# =============================================================================
# 3. FORMULARIOS
# =============================================================================

class RegistroUsuarioFormTests(TestCase):
    def datos(self, **extra):
        d = {'username': 'nuevo', 'email': 'nuevo@test.com', 'password1': PASSWORD, 'password2': PASSWORD}
        d.update(extra)
        return d

    def test_crea_usuario_con_rol_visitante(self):
        form = RegistroUsuarioForm(self.datos())
        self.assertTrue(form.is_valid(), form.errors)
        usuario = form.save()
        self.assertEqual(usuario.rol.nombre, Rol.VISITANTE)
        self.assertEqual(usuario.email, 'nuevo@test.com')
        self.assertTrue(usuario.check_password(PASSWORD))

    def test_email_obligatorio(self):
        self.assertFalse(RegistroUsuarioForm(self.datos(email='')).is_valid())

    def test_contrasenas_distintas(self):
        form = RegistroUsuarioForm(self.datos(password2='otra-clave-distinta-9'))
        self.assertFalse(form.is_valid())
        self.assertIn('password2', form.errors)

    def test_save_sin_commit_no_persiste(self):
        form = RegistroUsuarioForm(self.datos())
        form.is_valid()
        usuario = form.save(commit=False)
        self.assertIsNone(usuario.pk)

    def test_form_de_admin_asigna_rol_administrador(self):
        form = RegistroAdminForm(self.datos(username='jefe', email='jefe@test.com'))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().rol.nombre, Rol.ADMINISTRADOR)

    def test_form_de_admin_sin_commit(self):
        form = RegistroAdminForm(self.datos(username='jefe2', email='jefe2@test.com'))
        form.is_valid()
        self.assertIsNone(form.save(commit=False).pk)


class EspecieFormTests(TestCase):
    def test_normaliza_al_validar_y_guardar(self):
        form = EspecieForm({
            'nombre_cientifico': 'panthera   ONCA', 'nombre_comun': 'Yaguareté',
            'clase_taxonomica': 'mammalia', 'estado_conservacion': 'VU',
        })
        self.assertTrue(form.is_valid(), form.errors)
        especie = form.save()
        self.assertEqual(especie.nombre_cientifico, 'Panthera onca')
        self.assertEqual(especie.clase_taxonomica, 'Mammalia')

    def test_rechaza_duplicado(self):
        crear_especie('Panthera onca')
        form = EspecieForm({'nombre_cientifico': 'PANTHERA onca', 'estado_conservacion': 'NE'})
        self.assertFalse(form.is_valid())
        self.assertIn('nombre_cientifico', form.errors)

    def test_nombre_cientifico_obligatorio(self):
        self.assertFalse(EspecieForm({'estado_conservacion': 'NE'}).is_valid())


class EspecieFiltroFormTests(TestCase):
    def test_las_clases_salen_de_lo_cargado(self):
        crear_especie('Panthera onca', clase_taxonomica='Mammalia')
        crear_especie('Ramphastos toco', 'Tucán', clase_taxonomica='Aves')
        crear_especie('Sin clase', 'X', clase_taxonomica='')
        form = EspecieFiltroForm()
        valores = [v for v, _ in form.fields['clase'].choices]
        self.assertEqual(valores, ['', 'Aves', 'Mammalia'])

    def test_valida_estado_y_por_pagina(self):
        self.assertTrue(EspecieFiltroForm({'estado': 'VU', 'por_pagina': '25'}).is_valid())
        self.assertFalse(EspecieFiltroForm({'estado': 'ZZ'}).is_valid())
        self.assertFalse(EspecieFiltroForm({'por_pagina': '999'}).is_valid())


class AvistamientoFormTests(TestCase):
    def setUp(self):
        self.fuente = crear_fuente()
        self.especie = crear_especie()

    def datos(self, **extra):
        d = {
            'especie': self.especie.pk, 'fecha_avistamiento': '2026-05-09',
            'tipo_registro': 'avistamiento directo', 'estado_calidad': 'VALIDADO',
            'observaciones': 'Lo vio un guardaparque.',
        }
        d.update(extra)
        return d

    def test_carga_manual_minima_es_valida(self):
        form = AvistamientoForm(self.datos())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.es_manual)
        self.assertEqual(form.instance.origen_registro, Registro.OrigenRegistro.MANUAL)

    def test_carga_manual_exige_observaciones(self):
        form = AvistamientoForm(self.datos(observaciones=''))
        self.assertFalse(form.is_valid())
        self.assertIn('observaciones', form.errors)

    def test_estado_inicial_sugerido_es_validado(self):
        self.assertEqual(AvistamientoForm().initial['estado_calidad'], Registro.EstadoCalidad.VALIDADO)

    def test_noticia_nueva_exige_titulo_y_medio(self):
        form = AvistamientoForm(self.datos(noticia_url='https://medio.test/nueva'))
        self.assertFalse(form.is_valid())
        self.assertIn('noticia_titulo', form.errors)
        self.assertIn('noticia_fuente', form.errors)

    def test_noticia_existente_no_pide_titulo_ni_medio(self):
        crear_noticia(self.fuente, 'https://medio.test/existe')
        form = AvistamientoForm(self.datos(noticia_url='https://medio.test/existe'))
        self.assertTrue(form.is_valid(), form.errors)

    def test_ubicacion_parcial_es_invalida(self):
        form = AvistamientoForm(self.datos(lugar_nombre='Iguazú'))
        self.assertFalse(form.is_valid())
        self.assertIn('lugar_latitud', form.errors)
        self.assertIn('lugar_longitud', form.errors)

    def test_ubicacion_sin_nombre_pide_el_lugar(self):
        form = AvistamientoForm(self.datos(lugar_latitud='-25.6', lugar_longitud='-54.5'))
        self.assertFalse(form.is_valid())
        self.assertIn('lugar_nombre', form.errors)

    def test_rango_de_coordenadas(self):
        form = AvistamientoForm(self.datos(lugar_nombre='X', lugar_latitud='100', lugar_longitud='-54'))
        self.assertFalse(form.is_valid())
        self.assertIn('lugar_latitud', form.errors)

    def test_resolver_noticia_sin_url_devuelve_none(self):
        form = AvistamientoForm(self.datos())
        form.is_valid()
        self.assertIsNone(form.resolver_noticia())

    def test_resolver_noticia_crea_una_manual(self):
        form = AvistamientoForm(self.datos(
            noticia_url='https://medio.test/nueva', noticia_titulo='  Un   titulo ',
            noticia_fuente=self.fuente.pk, noticia_fecha_publicacion='2026-05-10',
        ))
        self.assertTrue(form.is_valid(), form.errors)
        noticia = form.resolver_noticia()
        self.assertEqual(noticia.titulo, 'Un titulo')
        self.assertEqual(noticia.origen, Noticia.Origen.MANUAL)
        self.assertEqual(noticia.estado_procesamiento, Noticia.EstadoProcesamiento.PROCESADA)
        self.assertEqual(noticia.fecha_publicacion, date(2026, 5, 10))

    def test_resolver_noticia_reutiliza_la_existente_sin_modificarla(self):
        existente = crear_noticia(self.fuente, 'https://medio.test/existe', titulo='Original')
        form = AvistamientoForm(self.datos(noticia_url='https://medio.test/existe', noticia_titulo='Otro'))
        form.is_valid()
        self.assertEqual(form.resolver_noticia().pk, existente.pk)
        existente.refresh_from_db()
        self.assertEqual(existente.titulo, 'Original')

    def test_resolver_noticia_corrige_la_noticia_del_propio_registro(self):
        noticia = crear_noticia(self.fuente, 'https://medio.test/propia', titulo='Viejo')
        registro = crear_registro(noticia)
        form = AvistamientoForm(
            self.datos(noticia_url='https://medio.test/propia', noticia_titulo='Nuevo título',
                       noticia_fuente=self.fuente.pk, noticia_fecha_publicacion='2026-06-01'),
            instance=registro,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.resolver_noticia()
        noticia.refresh_from_db()
        self.assertEqual(noticia.titulo, 'Nuevo título')
        self.assertEqual(noticia.fecha_publicacion, date(2026, 6, 1))

    def test_resolver_ubicacion_crea_y_reutiliza(self):
        datos = self.datos(lugar_nombre='Iguazú', lugar_zona='Selva', lugar_latitud='-25.6',
                           lugar_longitud='-54.5', lugar_precision='EXACTA')
        f1 = AvistamientoForm(datos)
        f1.is_valid()
        ub1 = f1.resolver_ubicacion()
        f2 = AvistamientoForm(datos)
        f2.is_valid()
        ub2 = f2.resolver_ubicacion()
        self.assertEqual(ub1.pk, ub2.pk)
        self.assertEqual(Ubicacion.objects.count(), 1)
        self.assertEqual(ub1.precision, 'EXACTA')

    def test_resolver_ubicacion_vacia_devuelve_none(self):
        form = AvistamientoForm(self.datos())
        form.is_valid()
        self.assertIsNone(form.resolver_ubicacion())

    def test_edicion_de_scraping_exige_url_de_noticia(self):
        registro = crear_registro()
        form = AvistamientoForm(self.datos(), instance=registro)
        self.assertFalse(form.es_manual)
        self.assertFalse(form.is_valid())
        self.assertIn('noticia_url', form.errors)

    def test_edicion_precarga_noticia_y_ubicacion(self):
        noticia = crear_noticia(self.fuente, 'https://medio.test/pre')
        ub = crear_ubicacion()
        registro = crear_registro(noticia, ubicacion=ub)
        form = AvistamientoForm(instance=registro)
        self.assertEqual(form.initial['noticia_url'], 'https://medio.test/pre')
        self.assertEqual(form.initial['noticia_fuente'], self.fuente.pk)
        self.assertEqual(form.initial['lugar_nombre'], 'Puerto Iguazú')
        self.assertEqual(form.initial['lugar_latitud'], -25.6)


class AvistamientoFiltroFormTests(SimpleTestCase):
    def test_rango_de_fechas_invertido(self):
        form = AvistamientoFiltroForm({'desde': '2026-06-01', 'hasta': '2026-05-01'})
        self.assertFalse(form.is_valid())
        self.assertIn('hasta', form.errors)

    def test_rango_valido(self):
        self.assertTrue(AvistamientoFiltroForm({'desde': '2026-05-01', 'hasta': '2026-06-01'}).is_valid())

    def test_sin_filtros_es_valido(self):
        self.assertTrue(AvistamientoFiltroForm({}).is_valid())


# =============================================================================
# 4. VISTAS
# =============================================================================

class PaginasGeneralesTests(TestCase):
    def test_index_es_publica(self):
        self.assertEqual(self.client.get(reverse('index')).status_code, 200)

    def test_login_get(self):
        self.assertEqual(self.client.get(reverse('login')).status_code, 200)

    def test_login_correcto_redirige(self):
        crear_visitante('ana')
        r = self.client.post(reverse('login'), {'username': 'ana', 'password': PASSWORD})
        self.assertRedirects(r, reverse('index'))

    def test_login_incorrecto_vuelve_a_mostrar_el_formulario(self):
        crear_visitante('ana')
        r = self.client.post(reverse('login'), {'username': 'ana', 'password': 'mala'})
        self.assertEqual(r.status_code, 200)

    def test_logout(self):
        crear_visitante('ana')
        self.client.login(username='ana', password=PASSWORD)
        r = self.client.post(reverse('logout'))
        self.assertRedirects(r, reverse('login'))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_registro_get(self):
        self.assertEqual(self.client.get(reverse('registro')).status_code, 200)

    def test_registro_crea_visitante_e_inicia_sesion(self):
        r = self.client.post(reverse('registro'), {
            'username': 'nuevo', 'email': 'n@test.com', 'password1': PASSWORD, 'password2': PASSWORD,
        })
        self.assertRedirects(r, reverse('index'))
        usuario = Usuario.objects.get(username='nuevo')
        self.assertEqual(usuario.rol.nombre, Rol.VISITANTE)
        self.assertEqual(int(self.client.session['_auth_user_id']), usuario.pk)

    def test_registro_invalido_no_crea_usuario(self):
        r = self.client.post(reverse('registro'), {
            'username': 'nuevo', 'email': 'n@test.com', 'password1': PASSWORD, 'password2': 'otra',
        })
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Usuario.objects.filter(username='nuevo').exists())

    def test_registro_con_sesion_iniciada_redirige_al_dashboard(self):
        crear_visitante('ana')
        self.client.login(username='ana', password=PASSWORD)
        self.assertRedirects(self.client.get(reverse('registro')), reverse('dashboard'))


class PermisosTests(TestCase):
    def test_anonimo_es_enviado_al_login(self):
        for nombre in ('dashboard', 'especies:lista', 'avistamientos:lista', 'especies:crear',
                       'avistamientos:crear', 'registro_admin'):
            with self.subTest(vista=nombre):
                r = self.client.get(reverse(nombre))
                self.assertEqual(r.status_code, 302)
                self.assertIn(reverse('login'), r.url)

    def test_visitante_puede_consultar(self):
        crear_visitante()
        self.client.login(username='visitante', password=PASSWORD)
        for nombre in ('especies:lista', 'avistamientos:lista', 'dashboard'):
            with self.subTest(vista=nombre):
                self.assertEqual(self.client.get(reverse(nombre)).status_code, 200)

    def test_visitante_no_puede_modificar(self):
        crear_visitante()
        especie = crear_especie()
        registro = crear_registro()
        self.client.login(username='visitante', password=PASSWORD)
        destinos = [
            reverse('especies:crear'), reverse('especies:editar', args=[especie.pk]),
            reverse('especies:eliminar', args=[especie.pk]), reverse('avistamientos:crear'),
            reverse('avistamientos:editar', args=[registro.pk]),
            reverse('avistamientos:eliminar', args=[registro.pk]), reverse('registro_admin'),
        ]
        for url in destinos:
            with self.subTest(url=url):
                r = self.client.post(url, {})
                self.assertRedirects(r, reverse('index'), fetch_redirect_response=False)
        self.assertTrue(Especie.objects.filter(pk=especie.pk).exists())
        self.assertTrue(Registro.objects.filter(pk=registro.pk).exists())

    def test_superusuario_pasa_el_control_de_admin(self):
        Usuario.objects.create_superuser('root', 'root@test.com', PASSWORD)
        self.client.login(username='root', password=PASSWORD)
        self.assertEqual(self.client.get(reverse('especies:crear')).status_code, 200)


class RegistroAdminVistaTests(TestCase):
    def test_admin_da_de_alta_a_otro_admin(self):
        crear_admin()
        self.client.login(username='admin', password=PASSWORD)
        self.assertEqual(self.client.get(reverse('registro_admin')).status_code, 200)
        r = self.client.post(reverse('registro_admin'), {
            'username': 'jefe', 'email': 'jefe@test.com', 'password1': PASSWORD, 'password2': PASSWORD,
        })
        self.assertRedirects(r, reverse('dashboard'))
        self.assertEqual(Usuario.objects.get(username='jefe').rol.nombre, Rol.ADMINISTRADOR)
        # No inicia sesión con la cuenta nueva
        self.assertEqual(Usuario.objects.get(pk=self.client.session['_auth_user_id']).username, 'admin')


class DashboardTests(TestCase):
    def setUp(self):
        self.admin = crear_admin()
        self.client.login(username='admin', password=PASSWORD)

    def test_dashboard_vacio(self):
        r = self.client.get(reverse('dashboard'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['kpis']['avistamientos'], 0)
        self.assertEqual(len(r.context['datos']['mensual']), 36)

    def test_dashboard_con_datos(self):
        esp = crear_especie()
        ub = crear_ubicacion(latitud=-25.6, longitud=-54.57)
        crear_registro(especie=esp, ubicacion=ub, fecha_avistamiento=timezone.localdate())
        crear_registro(especie=esp, estado_calidad=Registro.EstadoCalidad.VALIDADO, fecha_avistamiento=None)
        r = self.client.get(reverse('dashboard'))
        self.assertEqual(r.status_code, 200)
        kpis = r.context['kpis']
        self.assertEqual(kpis['especies'], 1)
        self.assertEqual(kpis['especies_con_avistamientos'], 1)
        self.assertEqual(kpis['avistamientos'], 2)
        self.assertEqual(kpis['ultimas_24h'], 2)
        self.assertEqual(kpis['pendientes'], 1)
        datos = r.context['datos']
        self.assertEqual(sum(m['n'] for m in datos['mensual']), 1)  # el otro no tiene fecha
        self.assertEqual({e['clave']: e['n'] for e in datos['estados']}['PENDIENTE'], 1)
        self.assertEqual(datos['clases'], [{'nombre': 'Mammalia', 'n': 1}])
        self.assertEqual(datos['top'][0]['nombre'], 'Yaguareté')
        self.assertEqual(datos['top'][0]['n'], 2)
        self.assertEqual(len(datos['registros']), 1)
        self.assertEqual(datos['registros'][0][:2], [-54.57, -25.6])
        self.assertEqual(len(r.context['ultimos']), 2)

    def test_el_mapa_descarta_puntos_fuera_de_misiones(self):
        ub = crear_ubicacion(nombre_lugar='Buenos Aires', latitud=-34.6, longitud=-58.4)
        crear_registro(ubicacion=ub)
        r = self.client.get(reverse('dashboard'))
        self.assertEqual(r.context['datos']['registros'], [])

    def test_especie_sin_nombre_comun_en_el_top(self):
        esp = crear_especie('Puma concolor', '')
        crear_registro(especie=esp)
        top = self.client.get(reverse('dashboard')).context['datos']['top']
        self.assertEqual(top[0], {'nombre': 'Puma concolor', 'cientifico': '', 'n': 1})

    def test_clase_vacia_se_muestra_como_sin_clasificar(self):
        crear_especie('Especie rara', 'X', clase_taxonomica='')
        clases = self.client.get(reverse('dashboard')).context['datos']['clases']
        self.assertEqual(clases[0]['nombre'], 'Sin clasificar')

    def test_dashboard_solo_acepta_get(self):
        self.assertEqual(self.client.post(reverse('dashboard')).status_code, 405)

    def test_dashboard_datos_devuelve_json(self):
        crear_registro()
        r = self.client.get(reverse('dashboard_datos'))
        self.assertEqual(r.status_code, 200)
        datos = r.json()
        self.assertEqual(datos['avistamientos'], 1)
        for clave in ('especies', 'especies_con_avistamientos', 'ultimas_24h', 'pendientes', 'actualizado'):
            self.assertIn(clave, datos)

    def test_dashboard_datos_exige_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('dashboard_datos')).status_code, 302)

    # BUG ENCONTRADO: views._ultimos_cargados() hace r.noticia.fecha_scraping, pero las
    # cargas manuales pueden no tener noticia (noticia = NULL) y el dashboard completo
    # falla con AttributeError. Cuando se corrija la vista (ver el aviso en la respuesta),
    # este test pasa a "unexpected success": ahí hay que borrarle el decorador.
    @expectedFailure
    def test_dashboard_con_avistamiento_manual_sin_noticia(self):
        crear_registro_manual(self.admin)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)


class EspecieVistasTests(TestCase):
    def setUp(self):
        self.admin = crear_admin()
        self.client.login(username='admin', password=PASSWORD)

    def test_lista_vacia(self):
        r = self.client.get(reverse('especies:lista'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['total'], 0)
        self.assertFalse(r.context['hay_filtros'])

    def test_lista_cuenta_avistamientos_por_especie(self):
        esp = crear_especie()
        crear_registro(especie=esp)
        crear_registro(especie=esp)
        crear_especie('Puma concolor', 'Puma')
        r = self.client.get(reverse('especies:lista'))
        conteos = {e.nombre_cientifico: e.num_avistamientos for e in r.context['page_obj']}
        self.assertEqual(conteos, {'Panthera onca': 2, 'Puma concolor': 0})

    def test_filtros(self):
        crear_especie('Panthera onca', 'Yaguareté', clase_taxonomica='Mammalia', estado_conservacion='VU')
        crear_especie('Ramphastos toco', 'Tucán', clase_taxonomica='Aves', estado_conservacion='LC')
        url = reverse('especies:lista')
        casos = [
            ({'q': 'tucan'}, 0), ({'q': 'tucán'}, 1), ({'q': 'panthera'}, 1),
            ({'clase': 'Aves'}, 1), ({'estado': 'VU'}, 1), ({'clase': 'Aves', 'estado': 'VU'}, 0),
        ]
        for params, esperado in casos:
            with self.subTest(params=params):
                r = self.client.get(url, params)
                self.assertEqual(r.context['total'], esperado)
                self.assertTrue(r.context['hay_filtros'])

    def test_filtros_invalidos_se_ignoran(self):
        crear_especie()
        r = self.client.get(reverse('especies:lista'), {'estado': 'ZZ', 'por_pagina': 'abc'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['total'], 1)

    def test_paginacion(self):
        for i in range(12):
            crear_especie(f'Genus species{chr(97 + i)}', f'Especie {i}')
        url = reverse('especies:lista')
        r1 = self.client.get(url)
        self.assertEqual((len(r1.context['page_obj']), r1.context['total']), (10, 12))
        r2 = self.client.get(url, {'page': 2})
        self.assertEqual(len(r2.context['page_obj']), 2)
        r3 = self.client.get(url, {'por_pagina': '25'})
        self.assertEqual(len(r3.context['page_obj']), 12)
        self.assertIn('por_pagina=25', self.client.get(url, {'por_pagina': '25', 'page': 1}).context['querystring'])

    def test_detalle(self):
        esp = crear_especie()
        for _ in range(12):
            crear_registro(especie=esp)
        r = self.client.get(reverse('especies:detalle', args=[esp.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['total_registros'], 12)
        self.assertEqual(len(r.context['registros']), 10)

    def test_detalle_inexistente(self):
        self.assertEqual(self.client.get(reverse('especies:detalle', args=[999])).status_code, 404)

    def test_crear(self):
        url = reverse('especies:crear')
        self.assertEqual(self.client.get(url).status_code, 200)
        r = self.client.post(url, {'nombre_cientifico': 'panthera ONCA', 'nombre_comun': 'Yaguareté',
                                   'estado_conservacion': 'VU'})
        esp = Especie.objects.get()
        self.assertEqual(esp.nombre_cientifico, 'Panthera onca')
        self.assertRedirects(r, reverse('especies:detalle', args=[esp.pk]))

    def test_crear_invalido_no_guarda(self):
        crear_especie('Panthera onca')
        r = self.client.post(reverse('especies:crear'), {'nombre_cientifico': 'Panthera onca',
                                                          'estado_conservacion': 'NE'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Especie.objects.count(), 1)

    def test_editar(self):
        esp = crear_especie()
        url = reverse('especies:editar', args=[esp.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        r = self.client.post(url, {'nombre_cientifico': 'Panthera onca', 'nombre_comun': 'Jaguar',
                                   'estado_conservacion': 'EN'})
        self.assertRedirects(r, reverse('especies:detalle', args=[esp.pk]))
        esp.refresh_from_db()
        self.assertEqual((esp.nombre_comun, esp.estado_conservacion), ('Jaguar', 'EN'))

    def test_editar_invalido(self):
        esp = crear_especie()
        r = self.client.post(reverse('especies:editar', args=[esp.pk]), {'nombre_cientifico': ''})
        self.assertEqual(r.status_code, 200)
        esp.refresh_from_db()
        self.assertEqual(esp.nombre_cientifico, 'Panthera onca')

    def test_eliminar_pide_confirmacion_y_borra(self):
        esp = crear_especie()
        url = reverse('especies:eliminar', args=[esp.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertTrue(Especie.objects.exists())
        self.assertRedirects(self.client.post(url), reverse('especies:lista'))
        self.assertFalse(Especie.objects.exists())

    def test_eliminar_especie_con_avistamientos_esta_protegido(self):
        esp = crear_especie()
        crear_registro(especie=esp)
        r = self.client.post(reverse('especies:eliminar', args=[esp.pk]))
        self.assertRedirects(r, reverse('especies:detalle', args=[esp.pk]))
        self.assertTrue(Especie.objects.filter(pk=esp.pk).exists())


class AvistamientoVistasTests(TestCase):
    def setUp(self):
        self.admin = crear_admin()
        self.client.login(username='admin', password=PASSWORD)
        self.fuente = crear_fuente('Primera Edición')
        self.especie = crear_especie()

    def payload(self, **extra):
        d = {
            'especie': self.especie.pk, 'fecha_avistamiento': '2026-05-09',
            'tipo_registro': 'avistamiento directo', 'estado_calidad': 'VALIDADO',
            'observaciones': 'Lo vio un guardaparque.',
        }
        d.update(extra)
        return d

    # --- listado -----------------------------------------------------------------
    def test_lista_vacia(self):
        r = self.client.get(reverse('avistamientos:lista'))
        self.assertEqual((r.status_code, r.context['total']), (200, 0))

    def test_lista_incluye_cargas_manuales_sin_noticia(self):
        crear_registro_manual(self.admin)
        crear_registro()
        self.assertEqual(self.client.get(reverse('avistamientos:lista')).context['total'], 2)

    def test_filtros_del_listado(self):
        otra = crear_especie('Ramphastos toco', 'Tucán')
        n1 = crear_noticia(self.fuente, 'https://medio.test/a')
        ub = crear_ubicacion(zona='Selva paranaense')
        crear_registro(n1, especie=self.especie, ubicacion=ub, fecha_avistamiento=date(2026, 5, 1),
                       estado_calidad=Registro.EstadoCalidad.PENDIENTE)
        crear_registro_manual(self.admin, especie=otra, fecha_avistamiento=date(2026, 6, 15),
                              estado_calidad=Registro.EstadoCalidad.VALIDADO)
        url = reverse('avistamientos:lista')
        casos = [
            ({'q': 'yagua'}, 1), ({'q': 'tucán'}, 1), ({'fuente': self.fuente.pk}, 1),
            ({'origen': 'MANUAL'}, 1), ({'origen': 'SCRAPING'}, 1), ({'estado': 'VALIDADO'}, 1),
            ({'zona': 'selva'}, 1), ({'desde': '2026-06-01'}, 1), ({'hasta': '2026-05-31'}, 1),
            ({'desde': '2026-05-01', 'hasta': '2026-06-30'}, 2), ({'q': 'inexistente'}, 0),
        ]
        for params, esperado in casos:
            with self.subTest(params=params):
                r = self.client.get(url, params)
                self.assertEqual(r.context['total'], esperado)
                self.assertTrue(r.context['hay_filtros'])

    def test_rango_de_fechas_invertido_no_rompe_el_listado(self):
        crear_registro()
        r = self.client.get(reverse('avistamientos:lista'), {'desde': '2026-06-01', 'hasta': '2026-05-01'})
        self.assertEqual(r.status_code, 200)

    def test_paginacion_del_listado(self):
        noticia = crear_noticia(self.fuente, 'https://medio.test/p')
        for _ in range(12):
            crear_registro(noticia)
        url = reverse('avistamientos:lista')
        self.assertEqual(len(self.client.get(url).context['page_obj']), 10)
        self.assertEqual(len(self.client.get(url, {'page': 2}).context['page_obj']), 2)

    # --- detalle -----------------------------------------------------------------
    def test_detalle_de_scraping(self):
        reg = crear_registro(especie=self.especie, ubicacion=crear_ubicacion())
        ExtraccionIA.objects.create(registro=reg, modelo='m', json_extraido={'a': 1},
                                    confianza=Decimal('0.9'))
        self.assertEqual(self.client.get(reverse('avistamientos:detalle', args=[reg.pk])).status_code, 200)

    def test_detalle_de_carga_manual_sin_noticia(self):
        reg = crear_registro_manual(self.admin)
        self.assertEqual(self.client.get(reverse('avistamientos:detalle', args=[reg.pk])).status_code, 200)

    def test_detalle_inexistente(self):
        self.assertEqual(self.client.get(reverse('avistamientos:detalle', args=[999])).status_code, 404)

    # --- alta --------------------------------------------------------------------
    def test_crear_precarga_la_especie_desde_la_url(self):
        r = self.client.get(reverse('avistamientos:crear'), {'especie': self.especie.pk})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['form'].initial['especie'], self.especie.pk)

    def test_crear_carga_manual_completa(self):
        r = self.client.post(reverse('avistamientos:crear'), self.payload(
            noticia_url='https://medio.test/nueva', noticia_titulo='Nota nueva',
            noticia_fuente=self.fuente.pk, lugar_nombre='Iguazú', lugar_zona='Selva',
            lugar_latitud='-25.6', lugar_longitud='-54.5',
        ))
        reg = Registro.objects.get()
        self.assertRedirects(r, reverse('avistamientos:detalle', args=[reg.pk]))
        self.assertEqual(reg.origen_registro, Registro.OrigenRegistro.MANUAL)
        self.assertEqual(reg.usuario_carga, self.admin)
        self.assertEqual(reg.usuario_validador, self.admin)
        self.assertIsNotNone(reg.fecha_validacion)
        self.assertEqual(reg.noticia.url, 'https://medio.test/nueva')
        self.assertEqual(reg.ubicacion.nombre_lugar, 'Iguazú')

    def test_crear_sin_noticia_ni_ubicacion(self):
        self.client.post(reverse('avistamientos:crear'), self.payload())
        reg = Registro.objects.get()
        self.assertIsNone(reg.noticia)
        self.assertIsNone(reg.ubicacion)

    def test_crear_pendiente_no_deja_validador(self):
        self.client.post(reverse('avistamientos:crear'), self.payload(estado_calidad='PENDIENTE'))
        reg = Registro.objects.get()
        self.assertIsNone(reg.usuario_validador)
        self.assertIsNone(reg.fecha_validacion)

    def test_crear_invalido_no_guarda(self):
        r = self.client.post(reverse('avistamientos:crear'), self.payload(observaciones=''))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Registro.objects.count(), 0)

    def test_el_origen_no_se_puede_forzar_desde_el_formulario(self):
        self.client.post(reverse('avistamientos:crear'), self.payload(origen_registro='SCRAPING',
                                                                      usuario_carga=999))
        self.assertEqual(Registro.objects.get().origen_registro, Registro.OrigenRegistro.MANUAL)

    # --- edición -----------------------------------------------------------------
    def test_editar_conserva_origen_scraping_y_registra_validador(self):
        noticia = crear_noticia(self.fuente, 'https://medio.test/e')
        reg = crear_registro(noticia, especie=self.especie)
        url = reverse('avistamientos:editar', args=[reg.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        r = self.client.post(url, self.payload(noticia_url=noticia.url, estado_calidad='DESCARTADO'))
        self.assertRedirects(r, reverse('avistamientos:detalle', args=[reg.pk]))
        reg.refresh_from_db()
        self.assertEqual(reg.origen_registro, Registro.OrigenRegistro.SCRAPING)
        self.assertIsNone(reg.usuario_carga)
        self.assertEqual(reg.estado_calidad, 'DESCARTADO')
        self.assertEqual(reg.usuario_validador, self.admin)

    def test_editar_a_pendiente_limpia_al_validador(self):
        reg = crear_registro_manual(self.admin, especie=self.especie,
                                    estado_calidad=Registro.EstadoCalidad.VALIDADO,
                                    usuario_validador=self.admin, fecha_validacion=timezone.now())
        self.client.post(reverse('avistamientos:editar', args=[reg.pk]), self.payload(estado_calidad='PENDIENTE'))
        reg.refresh_from_db()
        self.assertIsNone(reg.usuario_validador)
        self.assertIsNone(reg.fecha_validacion)

    def test_editar_invalido(self):
        reg = crear_registro_manual(self.admin)
        r = self.client.post(reverse('avistamientos:editar', args=[reg.pk]), self.payload(observaciones=''))
        self.assertEqual(r.status_code, 200)

    # --- baja --------------------------------------------------------------------
    def test_eliminar_pide_confirmacion_y_borra(self):
        reg = crear_registro()
        url = reverse('avistamientos:eliminar', args=[reg.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertTrue(Registro.objects.exists())
        self.assertRedirects(self.client.post(url), reverse('avistamientos:lista'))
        self.assertFalse(Registro.objects.exists())


# =============================================================================
# 5. ADMIN
# =============================================================================

class AdminTests(TestCase):
    def test_auditoria_es_de_solo_lectura(self):
        ma = AuditoriaAdmin(Auditoria, AdminSite())
        request = RequestFactory().get('/')
        self.assertFalse(ma.has_add_permission(request))
        self.assertFalse(ma.has_change_permission(request))
        self.assertFalse(ma.has_delete_permission(request))

    def test_listados_del_admin_de_django(self):
        Usuario.objects.create_superuser('root', 'root@test.com', PASSWORD)
        self.client.login(username='root', password=PASSWORD)
        crear_registro(especie=crear_especie(), ubicacion=crear_ubicacion())
        Auditoria.objects.create(tabla_afectada='x', id_registro_afectado=1, operacion='INSERT', usuario_bd='U')
        modelos = [m for m in django_admin.site._registry if m._meta.app_label == 'MisionaBio']
        self.assertGreaterEqual(len(modelos), 8)
        for modelo in modelos:
            url = reverse(f'admin:{modelo._meta.app_label}_{modelo._meta.model_name}_changelist')
            with self.subTest(modelo=modelo.__name__):
                self.assertEqual(self.client.get(url).status_code, 200)


# =============================================================================
# 6. SCRAPING
# =============================================================================

class NormalizarTextoTests(SimpleTestCase):
    def test_normalizar_texto(self):
        self.assertEqual(normalizar.normalizar_texto('  Yaguareté  '), 'yaguarete')
        self.assertEqual(normalizar.normalizar_texto(None), '')

    def test_en_misiones(self):
        self.assertTrue(normalizar.en_misiones(-27.36, -55.89))
        self.assertFalse(normalizar.en_misiones(-34.6, -58.4))
        self.assertFalse(normalizar.en_misiones(-27.0, -50.0))

    def test_normalizar_departamento(self):
        n = normalizar.normalizar_departamento
        self.assertEqual(n('Departamento Oberá'), 'Oberá')
        self.assertEqual(n('departamento de San Pedro'), 'San Pedro')
        self.assertEqual(n('iguazu'), 'Iguazú')
        self.assertEqual(n('Veinticinco de Mayo'), '25 de Mayo')
        self.assertEqual(n('Córdoba'), '')
        self.assertEqual(n(None), '')

    def test_parsear_fecha(self):
        p = normalizar.parsear_fecha
        self.assertEqual(p('2024-05-03'), date(2024, 5, 3))
        self.assertEqual(p('2024-05-03T10:30:00Z'), date(2024, 5, 3))
        self.assertEqual(p('2024-05-03T10:30:00-03:00'), date(2024, 5, 3))
        self.assertEqual(p('2024-05-03 texto extra'), date(2024, 5, 3))
        for malo in ('', None, 123, 'ayer', '2024-02-30', '2024-13-45'):
            with self.subTest(valor=malo):
                self.assertIsNone(p(malo))

    def test_tipo_de_registro(self):
        t = normalizar.normalizar_tipo_registro
        self.assertEqual(t('Cámara trampa'), 'cámara trampa')
        self.assertEqual(t('RASTRO'), 'rastro')
        self.assertEqual(t('algo raro'), 'otro')
        self.assertEqual(t(None), 'otro')

    def test_candidatos_cientificos(self):
        c = normalizar.nombres_cientificos_candidatos
        self.assertEqual(c('Yaguareté overo', 'panthera ONCA'), ['Panthera onca'])  # sin duplicar
        self.assertEqual(c('tucán', None), ['Ramphastos toco'])
        self.assertEqual(c('animal raro', 'felis catus'), ['Felis catus'])
        self.assertEqual(c('animal raro', None), [])
        self.assertEqual(c('animal raro', 'Genus'), [])  # un solo término no es binomial
        self.assertEqual(c('puma', 'Otro nombre'), ['Otro nombre', 'Puma concolor'])  # primero el de la IA


class ResolverFechaTests(SimpleTestCase):
    def test_usa_la_fecha_de_la_ia_si_es_valida(self):
        self.assertEqual(normalizar.resolver_fecha('2024-05-03', '2024-06-01'), (date(2024, 5, 3), 'ia'))

    def test_fecha_futura_de_la_ia_se_ignora(self):
        futura = (date.today() + timedelta(days=30)).isoformat()
        with mock.patch.object(config, 'FECHA_SI_FALTA', 'publicacion_o_hoy'):
            fecha, origen = normalizar.resolver_fecha(futura, '2024-06-01')
        self.assertEqual((fecha, origen), (date(2024, 6, 1), 'publicacion'))

    def test_modos_de_respaldo(self):
        hoy = date.today()
        casos = [
            ('publicacion_o_hoy', None, (hoy, 'hoy')),
            ('publicacion_o_hoy', '2024-06-01', (date(2024, 6, 1), 'publicacion')),
            ('hoy', '2024-06-01', (hoy, 'hoy')),
            ('publicacion', '2024-06-01', (date(2024, 6, 1), 'publicacion')),
            ('publicacion', None, (None, None)),
            ('nula', '2024-06-01', (None, None)),
            ('omitir', '2024-06-01', (None, None)),
        ]
        for modo, pub, esperado in casos:
            with self.subTest(modo=modo, pub=pub), mock.patch.object(config, 'FECHA_SI_FALTA', modo):
                self.assertEqual(normalizar.resolver_fecha(None, pub), esperado)

    def test_fecha_de_publicacion_futura_se_acota_a_hoy(self):
        futura = (date.today() + timedelta(days=10)).isoformat()
        with mock.patch.object(config, 'FECHA_SI_FALTA', 'publicacion'):
            self.assertEqual(normalizar.resolver_fecha(None, futura), (date.today(), 'publicacion'))


class NormalizarAvistamientoTests(SimpleTestCase):
    def crudo(self, **extra):
        d = {'nombre_comun': ' Yaguareté ', 'nombre_cientifico': 'Panthera onca',
             'lugar_texto': '  Parque   Nacional Iguazú ', 'fecha_avistamiento': '2026-05-03',
             'tipo_registro': 'Rastro', 'cita': 'Se vio al felino cerca del sendero'}
        d.update(extra)
        return d

    def test_avistamiento_valido(self):
        dato, motivo = normalizar.normalizar_avistamiento(self.crudo(), '2026-05-04')
        self.assertIsNone(motivo)
        self.assertEqual(dato['nombre_comun'], 'Yaguareté')
        self.assertEqual(dato['lugar_texto'], 'Parque Nacional Iguazú')
        self.assertEqual(dato['fecha'], date(2026, 5, 3))
        self.assertEqual(dato['fecha_origen'], 'ia')
        self.assertEqual(dato['tipo_registro'], 'rastro')
        self.assertEqual(dato['candidatos_cientificos'], ['Panthera onca'])

    def test_sin_nombre_comun_se_descarta(self):
        self.assertEqual(normalizar.normalizar_avistamiento(self.crudo(nombre_comun='  '), None),
                         (None, 'sin nombre común'))

    def test_sin_lugar_solo_se_descarta_si_la_ubicacion_es_obligatoria(self):
        dato, _ = normalizar.normalizar_avistamiento(self.crudo(lugar_texto=''), None)
        self.assertIsNotNone(dato)
        with mock.patch.object(config, 'REQUIERE_UBICACION', True):
            self.assertEqual(normalizar.normalizar_avistamiento(self.crudo(lugar_texto=''), None),
                             (None, 'sin lugar'))

    def test_sin_fecha_valida_con_modo_omitir(self):
        with mock.patch.object(config, 'FECHA_SI_FALTA', 'omitir'):
            self.assertEqual(normalizar.normalizar_avistamiento(self.crudo(fecha_avistamiento=None), None),
                             (None, 'sin fecha válida'))

    def test_la_cita_se_recorta_a_25_palabras(self):
        dato, _ = normalizar.normalizar_avistamiento(self.crudo(cita=' '.join(['palabra'] * 40)), None)
        self.assertEqual(len(dato['cita'].split()), 25)

    def test_el_lugar_se_recorta_a_200_caracteres(self):
        dato, _ = normalizar.normalizar_avistamiento(self.crudo(lugar_texto='a' * 500), None)
        self.assertEqual(len(dato['lugar_texto']), 200)

    def test_valores_que_no_son_texto(self):
        dato, _ = normalizar.normalizar_avistamiento(
            self.crudo(cita=None, tipo_registro=5, nombre_cientifico=7), None)
        self.assertEqual(dato['cita'], '')
        self.assertEqual(dato['tipo_registro'], 'otro')


class ConfianzaTests(SimpleTestCase):
    def av(self, **extra):
        d = {'cita': 'vimos un yaguareté cruzar el camino', 'nombre_comun': 'yaguareté',
             'lugar_texto': 'Puerto Iguazú', 'fecha_origen': 'ia', 'candidatos_cientificos': ['Panthera onca']}
        d.update(extra)
        return d

    TEXTO = 'Ayer vimos un yaguareté cruzar el camino cerca de Puerto Iguazú, dijo un vecino.'

    def test_presencia_frase_completa(self):
        self.assertEqual(confianza._presencia('Puerto Iguazú', 'cerca de puerto iguazu hoy'), 1.0)

    def test_presencia_tolera_plurales(self):
        self.assertEqual(confianza._presencia('tucán', 'vimos tucanes en el monte'), 1.0)

    def test_presencia_parcial(self):
        self.assertEqual(
            confianza._presencia('tucan toco grande hermoso azul', 'el tucan toco grande hermoso paso'), 0.5)

    def test_presencia_ausente_o_vacia(self):
        self.assertEqual(confianza._presencia('yaguarete', 'texto sin relacion'), 0.0)
        self.assertEqual(confianza._presencia('', 'cualquier texto'), 0.0)
        self.assertEqual(confianza._presencia('de la', 'texto sin relacion'), 0.0)  # solo palabras cortas

    def test_extraccion_perfecta_tiene_confianza_1(self):
        geo = geocodificar.Punto(-25.6, -54.5, 'diccionario', 'Puerto Iguazú', 'Iguazú')
        valor, controles = confianza.calcular_confianza(self.av(), self.TEXTO, True, geo)
        self.assertEqual(valor, 1.0)
        self.assertEqual(controles['cita_en_texto'], 1.0)
        self.assertEqual(controles['geocodificacion'], 1.0)

    def test_extraccion_sin_respaldo_tiene_confianza_0(self):
        av = self.av(cita='', nombre_comun='', lugar_texto='', fecha_origen=None, candidatos_cientificos=[])
        valor, controles = confianza.calcular_confianza(av, self.TEXTO, False, None)
        self.assertEqual(valor, 0.0)
        self.assertEqual(controles['especie_en_catalogo'], 0.0)
        self.assertEqual(controles['lugar_en_texto'], 0.0)

    def test_puntaje_de_especie_fuera_del_catalogo(self):
        _, controles = confianza.calcular_confianza(self.av(), self.TEXTO, False, None)
        self.assertEqual(controles['especie_en_catalogo'], 0.3)

    def test_geocodificacion_externa_vale_menos_que_el_diccionario(self):
        geo = geocodificar.Punto(-25.6, -54.5, 'nominatim', 'X', '')
        _, controles = confianza.calcular_confianza(self.av(), self.TEXTO, True, geo)
        self.assertEqual(controles['geocodificacion'], 0.6)

    def test_el_origen_de_la_fecha_cambia_el_puntaje(self):
        for origen, esperado in (('ia', 1.0), ('publicacion', 0.4), ('hoy', 0.2), ('otro', 0.0)):
            with self.subTest(origen=origen):
                _, c = confianza.calcular_confianza(self.av(fecha_origen=origen), self.TEXTO, True, None)
                self.assertEqual(c['fecha'], esperado)

    def test_el_resultado_siempre_esta_entre_0_y_1(self):
        valor, _ = confianza.calcular_confianza(self.av(), self.TEXTO, True, None)
        self.assertTrue(0.0 <= valor <= 1.0)


class ExtraerIATests(SimpleTestCase):
    def test_filtro_previo(self):
        self.assertTrue(extraer_ia.parece_relevante('Avistaron un yaguareté', 'en el monte'))
        self.assertTrue(extraer_ia.parece_relevante('Nota', 'Se vio un TUCÁN en la plaza'))
        self.assertFalse(extraer_ia.parece_relevante('Receta de torta', 'harina y azucar'))

    def test_parsear_json_con_valla_de_codigo(self):
        crudo = '```json\n{"avistamientos": [{"nombre_comun": "tucán"}, "basura", 5]}\n```'
        self.assertEqual(extraer_ia._parsear_json(crudo), [{'nombre_comun': 'tucán'}])

    def test_parsear_json_lista_directa(self):
        self.assertEqual(extraer_ia._parsear_json('[{"a": 1}, {"b": 2}]'), [{'a': 1}, {'b': 2}])

    def test_parsear_json_sin_clave_o_tipo_inesperado(self):
        self.assertEqual(extraer_ia._parsear_json('{"otra": 1}'), [])
        self.assertEqual(extraer_ia._parsear_json('"texto"'), [])
        self.assertEqual(extraer_ia._parsear_json('42'), [])

    def test_parsear_json_invalido_lanza_error(self):
        with self.assertRaises(ValueError):
            extraer_ia._parsear_json('esto no es json')

    def test_cliente_sin_api_key(self):
        with mock.patch.object(extraer_ia, '_client', None), mock.patch.object(config, 'GEMINI_API_KEY', ''):
            with self.assertRaises(RuntimeError):
                extraer_ia._cliente()

    def _cliente_falso(self, *respuestas):
        cliente = mock.Mock()
        efectos = [r if isinstance(r, Exception) else mock.Mock(text=r) for r in respuestas]
        cliente.models.generate_content.side_effect = efectos
        return cliente

    def test_extraer_avistamientos_ok(self):
        cliente = self._cliente_falso('{"avistamientos": [{"nombre_comun": "puma"}]}')
        with mock.patch.object(extraer_ia, '_cliente', return_value=cliente), \
                mock.patch.object(extraer_ia.time, 'sleep'):
            res = extraer_ia.extraer_avistamientos('Titulo', '2026-05-01', 'Texto ' * 5000)
        self.assertEqual(res, [{'nombre_comun': 'puma'}])
        prompt = cliente.models.generate_content.call_args.kwargs['contents']
        self.assertIn('Titulo', prompt)
        self.assertLessEqual(len(prompt), len(extraer_ia.PROMPT) + config.MAX_CHARS_TEXTO + 500)

    def test_extraer_avistamientos_reintenta_si_el_json_es_invalido(self):
        cliente = self._cliente_falso('no es json', '{"avistamientos": []}')
        with mock.patch.object(extraer_ia, '_cliente', return_value=cliente), \
                mock.patch.object(extraer_ia.time, 'sleep'), silenciar():
            res = extraer_ia.extraer_avistamientos('T', None, 'texto')
        self.assertEqual(res, [])
        self.assertEqual(cliente.models.generate_content.call_count, 2)

    def test_extraer_avistamientos_devuelve_none_si_siempre_falla(self):
        cliente = self._cliente_falso(Exception('cuota'), Exception('cuota'))
        with mock.patch.object(extraer_ia, '_cliente', return_value=cliente), \
                mock.patch.object(extraer_ia.time, 'sleep'), \
                mock.patch.object(config, 'GEMINI_REINTENTOS', 2), silenciar():
            self.assertIsNone(extraer_ia.extraer_avistamientos('T', '2026-05-01', 'texto'))

    def test_extraer_avistamientos_sin_clave_devuelve_none(self):
        with mock.patch.object(extraer_ia, '_client', None), mock.patch.object(config, 'GEMINI_API_KEY', ''), \
                mock.patch.object(extraer_ia.time, 'sleep'), mock.patch.object(config, 'GEMINI_REINTENTOS', 1), \
                silenciar():
            self.assertIsNone(extraer_ia.extraer_avistamientos('T', None, 'texto'))


HTML_NOTA = """
<html><head>
  <title>Titulo de la pagina</title>
  <meta property="og:title" content="  Avistan un yaguareté en Iguazú ">
  <meta property="article:published_time" content="2026-05-03T09:00:00-03:00">
</head><body>
  <nav><p>Menú de navegación con un párrafo largo que no debería aparecer nunca jamás</p></nav>
  <article>
    <p>Corto.</p>
    <p>Un yaguareté fue visto por guardaparques en el Parque Nacional Iguazú durante la mañana.</p>
    <script>var basura = "este script no debe aparecer en el texto de la nota para nada";</script>
    <p>Los especialistas destacaron la importancia del registro para la conservación de la especie.</p>
  </article>
  <footer><p>Pie de página con un texto bastante largo que tampoco debería formar parte de la nota</p></footer>
</body></html>
"""


class DescargarTests(SimpleTestCase):
    def setUp(self):
        descargar._robots_cache.clear()
        self.addCleanup(descargar._robots_cache.clear)
        for k in descargar.ESTADISTICAS:
            descargar.ESTADISTICAS[k] = 0

    def test_extraer_nota_completa(self):
        nota = descargar.extraer_nota(HTML_NOTA)
        self.assertEqual(nota['titulo'], 'Avistan un yaguareté en Iguazú')
        self.assertEqual(nota['fecha'], '2026-05-03T09:00:00-03:00')
        self.assertIn('Parque Nacional Iguazú', nota['texto'])
        self.assertIn('conservación de la especie', nota['texto'])
        self.assertNotIn('Corto.', nota['texto'])        # párrafos de 40 caracteres o menos
        self.assertNotIn('basura', nota['texto'])        # scripts
        self.assertNotIn('Menú', nota['texto'])          # nav fuera del <article>
        self.assertNotIn('Pie de página', nota['texto'])

    def test_titulo_de_respaldo_desde_title(self):
        nota = descargar.extraer_nota('<html><head><title> Solo titulo </title></head><body></body></html>')
        self.assertEqual(nota['titulo'], 'Solo titulo')
        self.assertEqual(nota['texto'], '')

    def test_html_vacio(self):
        self.assertEqual(descargar.extraer_nota('<html></html>'), {'titulo': '', 'fecha': '', 'texto': ''})

    def test_fecha_desde_json_ld(self):
        html = ('<html><head><script type="application/ld+json">'
                '{"@type": "NewsArticle", "datePublished": "2026-04-01T10:00:00Z"}'
                '</script></head></html>')
        self.assertEqual(descargar.extraer_nota(html)['fecha'], '2026-04-01T10:00:00Z')

    def test_fecha_desde_etiqueta_time(self):
        html = '<html><body><time datetime=" 2026-03-02 ">2 de marzo</time></body></html>'
        self.assertEqual(descargar.extraer_nota(html)['fecha'], '2026-03-02')

    def _respuesta(self, status=200, texto=''):
        return mock.Mock(status_code=status, text=texto)

    def test_robots_bloquea_y_permite(self):
        robots = 'User-agent: *\nDisallow: /privado/'
        with mock.patch.object(descargar.sesion, 'get', return_value=self._respuesta(200, robots)):
            self.assertTrue(descargar.permitido_por_robots('https://medio.test/nota'))
            self.assertFalse(descargar.permitido_por_robots('https://medio.test/privado/x'))

    def test_robots_se_consulta_una_sola_vez_por_sitio(self):
        with mock.patch.object(descargar.sesion, 'get', return_value=self._respuesta(200, '')) as get:
            descargar.permitido_por_robots('https://medio.test/a')
            descargar.permitido_por_robots('https://medio.test/b')
        self.assertEqual(get.call_count, 1)

    def test_sin_robots_txt_se_permite_todo(self):
        with mock.patch.object(descargar.sesion, 'get', return_value=self._respuesta(404)):
            self.assertTrue(descargar.permitido_por_robots('https://medio.test/privado/x'))

    def test_error_de_red_al_pedir_robots_permite(self):
        with mock.patch.object(descargar.sesion, 'get', side_effect=requests.ConnectionError()):
            self.assertTrue(descargar.permitido_por_robots('https://otro.test/x'))

    def test_descargar_ok(self):
        resp = mock.Mock(text='<html>ok</html>')
        with mock.patch.object(descargar, 'permitido_por_robots', return_value=True), \
                mock.patch.object(descargar.sesion, 'get', return_value=resp), \
                mock.patch.object(descargar.time, 'sleep'):
            self.assertEqual(descargar.descargar('https://medio.test/a'), '<html>ok</html>')
        self.assertEqual(descargar.ESTADISTICAS['descargas_ok'], 1)

    def test_descargar_con_error_http(self):
        resp = mock.Mock()
        resp.raise_for_status.side_effect = requests.HTTPError('500')
        with mock.patch.object(descargar, 'permitido_por_robots', return_value=True), \
                mock.patch.object(descargar.sesion, 'get', return_value=resp), \
                mock.patch.object(descargar.time, 'sleep'), silenciar():
            self.assertIsNone(descargar.descargar('https://medio.test/a'))
        self.assertEqual(descargar.ESTADISTICAS['descargas_error'], 1)

    def test_descargar_respeta_robots(self):
        with mock.patch.object(descargar, 'permitido_por_robots', return_value=False), \
                mock.patch.object(descargar.sesion, 'get') as get, \
                mock.patch.object(descargar.time, 'sleep'), silenciar():
            self.assertIsNone(descargar.descargar('https://medio.test/privado'))
        get.assert_not_called()


RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Feed</title>
<item><title>A</title><link>https://medio.test/a</link></item>
<item><title>B</title><link>https://medio.test/b</link></item>
</channel></rss>"""


class DescubrirTests(SimpleTestCase):
    def test_procesadas_se_guardan_y_se_leen(self):
        with tempfile.TemporaryDirectory() as tmp:
            archivo = Path(tmp) / 'procesadas.txt'
            with mock.patch.object(config, 'PROCESADAS', archivo):
                self.assertEqual(descubrir.cargar_procesadas(), set())
                descubrir.marcar_procesada('https://medio.test/a')
                descubrir.marcar_procesada('https://medio.test/b')
                self.assertEqual(descubrir.cargar_procesadas(), {'https://medio.test/a', 'https://medio.test/b'})

    def test_urls_de_feed_recorre_paginas_hasta_que_no_hay_mas(self):
        with mock.patch.object(descubrir, 'descargar', side_effect=[RSS, None]) as desc, \
                mock.patch.object(config, 'PAGINAS_POR_FEED', 3), silenciar():
            urls = descubrir.urls_de_feed('Medio', 'https://medio.test/feed/')
        self.assertEqual(urls, [('Medio', 'https://medio.test/a'), ('Medio', 'https://medio.test/b')])
        self.assertEqual(desc.call_args_list[1].args[0], 'https://medio.test/feed/?paged=2')

    def test_urls_de_feed_con_parametros_en_la_url(self):
        with mock.patch.object(descubrir, 'descargar', side_effect=[RSS, None]) as desc, \
                mock.patch.object(config, 'PAGINAS_POR_FEED', 2), silenciar():
            descubrir.urls_de_feed('Medio', 'https://medio.test/?feed=rss2')
        self.assertEqual(desc.call_args_list[1].args[0], 'https://medio.test/?feed=rss2&paged=2')

    def test_urls_de_feed_sin_entradas(self):
        with mock.patch.object(descubrir, 'descargar', return_value='<rss><channel></channel></rss>'), \
                silenciar():
            self.assertEqual(descubrir.urls_de_feed('Medio', 'https://medio.test/feed/'), [])

    def test_busqueda_por_api_wordpress(self):
        respuesta = json.dumps([{'link': 'https://medio.test/1'}, {'link': 'https://medio.test/2'}])
        with mock.patch.object(descubrir, 'descargar', return_value=respuesta) as desc, \
                mock.patch.object(config, 'PAGINAS_POR_BUSQUEDA', 1), silenciar():
            urls = descubrir.urls_de_busqueda('Medio', 'https://medio.test', 'yaguareté')
        self.assertEqual(len(urls), 2)
        self.assertIn('wp-json/wp/v2/posts?search=yaguaret', desc.call_args.args[0])

    def test_medio_nacional_agrega_misiones_a_la_busqueda(self):
        with mock.patch.object(descubrir, 'descargar', return_value='[]') as desc, \
                mock.patch.object(config, 'SITIOS_NACIONALES', {'Nacional'}), \
                mock.patch.object(config, 'PAGINAS_POR_BUSQUEDA', 1), silenciar():
            descubrir.urls_de_busqueda('Nacional', 'https://nacional.test', 'tucan')
        self.assertIn('tucan%20Misiones', desc.call_args.args[0])

    def test_busqueda_cae_al_rss_si_la_api_falla(self):
        with mock.patch.object(descubrir, 'descargar', side_effect=['no es json', RSS]) as desc, \
                mock.patch.object(config, 'PAGINAS_POR_BUSQUEDA', 1), silenciar():
            urls = descubrir.urls_de_busqueda('Medio', 'https://medio.test', 'tucan')
        self.assertEqual(len(urls), 2)
        self.assertIn('feed=rss2', desc.call_args_list[1].args[0])

    def test_busqueda_sin_resultados_corta(self):
        with mock.patch.object(descubrir, 'descargar', return_value=None), \
                mock.patch.object(config, 'PAGINAS_POR_BUSQUEDA', 3), silenciar():
            self.assertEqual(descubrir.urls_de_busqueda('Medio', 'https://medio.test', 'x'), [])

    def test_descubrir_deduplica_filtra_y_aplica_tope(self):
        larga = 'https://medio.test/' + 'x' * 600
        with mock.patch.object(descubrir, 'urls_de_feed',
                               return_value=[('F', 'https://m/1'), ('F', 'https://m/2'), ('F', larga)]), \
                mock.patch.object(descubrir, 'urls_de_busqueda',
                                  return_value=[('S', 'https://m/2'), ('S', 'https://m/3'), ('S', 'https://m/4')]), \
                mock.patch.object(config, 'FEEDS', {'F': 'u'}), mock.patch.object(config, 'SITIOS_WP', {'S': 'b'}), \
                mock.patch.object(config, 'TERMINOS_BUSQUEDA', ['t']), \
                mock.patch.object(config, 'URLS_MANUALES', ['https://m/5']), \
                mock.patch.object(config, 'MAX_NOTAS_POR_CORRIDA', 10), silenciar():
            res = descubrir.descubrir(ya_procesadas={'https://m/3'})
        self.assertEqual([u for _, u in res], ['https://m/1', 'https://m/2', 'https://m/4', 'https://m/5'])

        with mock.patch.object(descubrir, 'urls_de_feed', return_value=[('F', f'https://m/{i}') for i in range(5)]), \
                mock.patch.object(descubrir, 'urls_de_busqueda', return_value=[]), \
                mock.patch.object(config, 'FEEDS', {'F': 'u'}), mock.patch.object(config, 'SITIOS_WP', {}), \
                mock.patch.object(config, 'URLS_MANUALES', []), \
                mock.patch.object(config, 'MAX_NOTAS_POR_CORRIDA', 2), silenciar():
            self.assertEqual(len(descubrir.descubrir(ya_procesadas=set())), 2)

    def test_descubrir_sin_conjunto_usa_el_archivo(self):
        with mock.patch.object(descubrir, 'cargar_procesadas', return_value={'https://m/1'}) as cargar, \
                mock.patch.object(descubrir, 'urls_de_feed', return_value=[('F', 'https://m/1')]), \
                mock.patch.object(config, 'FEEDS', {'F': 'u'}), mock.patch.object(config, 'SITIOS_WP', {}), \
                mock.patch.object(config, 'URLS_MANUALES', []), silenciar():
            self.assertEqual(descubrir.descubrir(), [])
        cargar.assert_called_once()


class GeocodificarTests(SimpleTestCase):
    def test_desde_diccionario_elige_el_lugar_mas_especifico(self):
        coords, nombre, depto = geocodificar.desde_diccionario('lo vieron en posadas')
        self.assertEqual((nombre, depto), ('Posadas', 'Capital'))
        self.assertEqual(coords, (-27.3671, -55.8961))

    def test_desde_diccionario_sin_coincidencia(self):
        self.assertIsNone(geocodificar.desde_diccionario('un lugar inexistente xyz'))

    def test_desde_diccionario_no_confunde_subcadenas(self):
        self.assertIsNone(geocodificar.desde_diccionario('posadasxyz'))

    def test_geocodificar_texto_vacio(self):
        self.assertIsNone(geocodificar.geocodificar(''))
        self.assertIsNone(geocodificar.geocodificar(None))

    def test_geocodificar_usa_el_diccionario(self):
        p = geocodificar.geocodificar('Cerca de Posadas, Misiones')
        self.assertEqual((p.metodo, p.zona, p.departamento), ('diccionario', 'Posadas', 'Capital'))

    def test_geocodificar_desde_cache_en_los_tres_formatos(self):
        cache = {
            'lugar a': [-27.0, -55.0], 'lugar b': [-27.1, -55.1, 'Pueblo B'],
            'lugar c': [-27.2, -55.2, 'Pueblo C', 'Oberá'], 'lugar d': None,
        }
        with mock.patch.object(geocodificar, '_cache', cache):
            a = geocodificar.geocodificar('lugar a')
            self.assertEqual((a.metodo, a.zona, a.departamento), ('cache', '', ''))
            self.assertEqual(geocodificar.geocodificar('lugar b').zona, 'Pueblo B')
            self.assertEqual(geocodificar.geocodificar('lugar c').departamento, 'Oberá')
            self.assertIsNone(geocodificar.geocodificar('lugar d'))

    def test_sin_nominatim_no_se_consulta_la_red(self):
        with mock.patch.object(geocodificar, '_cache', {}), mock.patch.object(config, 'USAR_NOMINATIM', False), \
                mock.patch.object(geocodificar, '_desde_nominatim') as nom:
            self.assertIsNone(geocodificar.geocodificar('lugar desconocido'))
        nom.assert_not_called()

    def test_nominatim_guarda_en_cache(self):
        with mock.patch.object(geocodificar, '_cache', {}) as cache, \
                mock.patch.object(config, 'USAR_NOMINATIM', True), \
                mock.patch.object(geocodificar, '_guardar_cache') as guardar_cache, \
                mock.patch.object(geocodificar, '_desde_nominatim', return_value=(-27.0, -55.0, 'Pueblo', 'Oberá')):
            p = geocodificar.geocodificar('Lugar Nuevo')
            self.assertEqual((p.metodo, p.zona, p.departamento), ('nominatim', 'Pueblo', 'Oberá'))
            self.assertEqual(cache['lugar nuevo'], [-27.0, -55.0, 'Pueblo', 'Oberá'])
        guardar_cache.assert_called_once()

    def test_nominatim_fuera_de_misiones_se_descarta_y_se_cachea_el_fallo(self):
        with mock.patch.object(geocodificar, '_cache', {}) as cache, \
                mock.patch.object(config, 'USAR_NOMINATIM', True), \
                mock.patch.object(geocodificar, '_guardar_cache'), \
                mock.patch.object(geocodificar, '_desde_nominatim', return_value=(-34.6, -58.4, 'BA', '')):
            self.assertIsNone(geocodificar.geocodificar('Buenos Aires'))
            self.assertIn('buenos aires', cache)
            self.assertIsNone(cache['buenos aires'])

    def test_desde_nominatim_interpreta_la_respuesta(self):
        respuesta = mock.Mock()
        respuesta.json.return_value = [{'lat': '-27.48', 'lon': '-55.12',
                                        'address': {'town': 'Oberá', 'county': 'Departamento Oberá'}}]
        with mock.patch.object(geocodificar.requests, 'get', return_value=respuesta), \
                mock.patch.object(geocodificar.time, 'sleep'):
            self.assertEqual(geocodificar._desde_nominatim('Oberá'), (-27.48, -55.12, 'Oberá', 'Oberá'))

    def test_desde_nominatim_sin_resultados(self):
        respuesta = mock.Mock()
        respuesta.json.return_value = []
        with mock.patch.object(geocodificar.requests, 'get', return_value=respuesta), \
                mock.patch.object(geocodificar.time, 'sleep'):
            self.assertIsNone(geocodificar._desde_nominatim('nada'))

    def test_desde_nominatim_con_error_de_red(self):
        with mock.patch.object(geocodificar.requests, 'get', side_effect=requests.Timeout()), \
                mock.patch.object(geocodificar.time, 'sleep'):
            self.assertIsNone(geocodificar._desde_nominatim('nada'))

    def test_desde_nominatim_con_respuesta_malformada(self):
        respuesta = mock.Mock()
        respuesta.json.return_value = [{'sin': 'lat'}]
        with mock.patch.object(geocodificar.requests, 'get', return_value=respuesta), \
                mock.patch.object(geocodificar.time, 'sleep'):
            self.assertIsNone(geocodificar._desde_nominatim('nada'))

    def test_cargar_cache_desde_archivo(self):
        with tempfile.TemporaryDirectory() as tmp:
            archivo = Path(tmp) / 'cache.json'
            archivo.write_text('{"x": [1, 2]}', encoding='utf-8')
            with mock.patch.object(geocodificar, '_cache', None), mock.patch.object(config, 'GEOCACHE', archivo):
                self.assertEqual(geocodificar._cargar_cache(), {'x': [1, 2]})

    def test_cargar_cache_con_archivo_inexistente_o_roto(self):
        with tempfile.TemporaryDirectory() as tmp:
            roto = Path(tmp) / 'roto.json'
            roto.write_text('{no es json', encoding='utf-8')
            for archivo in (Path(tmp) / 'no-existe.json', roto):
                with mock.patch.object(geocodificar, '_cache', None), mock.patch.object(config, 'GEOCACHE', archivo):
                    self.assertEqual(geocodificar._cargar_cache(), {})

    def test_guardar_cache_escribe_el_archivo(self):
        with tempfile.TemporaryDirectory() as tmp:
            archivo = Path(tmp) / 'cache.json'
            with mock.patch.object(geocodificar, '_cache', {'lugar': [1, 2, 'Z', '']}), \
                    mock.patch.object(config, 'GEOCACHE', archivo):
                geocodificar._guardar_cache()
            self.assertEqual(json.loads(archivo.read_text(encoding='utf-8')), {'lugar': [1, 2, 'Z', '']})


class GuardarTests(TestCase):
    def setUp(self):
        guardar._fuentes_cache.clear()
        guardar._catalogo = None
        self.addCleanup(guardar._fuentes_cache.clear)
        self.addCleanup(setattr, guardar, '_catalogo', None)

    def nota(self, **extra):
        d = {'titulo': '  Avistan   un yaguareté ', 'fecha': '2026-05-03T09:00:00-03:00',
             'texto': 'Un yaguareté fue visto. ' * 100}
        d.update(extra)
        return d

    def av(self, **extra):
        d = {'fecha': date(2026, 5, 3), 'tipo_registro': 'avistamiento directo',
             'cita': 'Un yaguareté fue visto'}
        d.update(extra)
        return d

    def geo(self, lat=-25.6, lon=-54.5, zona='Puerto Iguazú', depto='Iguazú'):
        return geocodificar.Punto(lat, lon, 'diccionario', zona, depto)

    # --- fuentes y noticias -----------------------------------------------------
    def test_obtener_fuente_de_dominio_desconocido(self):
        fuente = guardar.obtener_fuente('https://www.ejemplo.com/nota/1')
        self.assertEqual((fuente.nombre, fuente.url_base), ('ejemplo.com', 'https://www.ejemplo.com'))

    def test_obtener_fuente_conocida_usa_el_nombre_del_medio(self):
        nombre, base = next(iter(config.SITIOS_WP.items()))
        fuente = guardar.obtener_fuente(f'{base}/nota/1')
        self.assertEqual((fuente.nombre, fuente.url_base), (nombre, base))

    def test_obtener_fuente_no_duplica(self):
        a = guardar.obtener_fuente('https://www.ejemplo.com/a')
        b = guardar.obtener_fuente('https://www.ejemplo.com/b')
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(Fuente.objects.count(), 1)

    def test_registrar_noticia_relevante(self):
        noticia = guardar.registrar_noticia('https://medio.test/a', crear_fuente(), self.nota(), True)
        self.assertEqual(noticia.titulo, 'Avistan un yaguareté')
        self.assertEqual(noticia.fecha_publicacion, date(2026, 5, 3))
        self.assertEqual(noticia.estado_procesamiento, Noticia.EstadoProcesamiento.PENDIENTE)
        self.assertEqual(len(noticia.extracto), config.EXTRACTO_CHARS)  # nunca el texto completo
        self.assertEqual(noticia.origen, Noticia.Origen.SCRAPING)

    def test_registrar_noticia_no_relevante_queda_descartada_sin_extracto(self):
        noticia = guardar.registrar_noticia('https://medio.test/a', crear_fuente(), self.nota(), False)
        self.assertEqual(noticia.estado_procesamiento, Noticia.EstadoProcesamiento.DESCARTADA)
        self.assertEqual(noticia.extracto, '')

    def test_registrar_noticia_actualiza_en_vez_de_duplicar(self):
        fuente = crear_fuente()
        guardar.registrar_noticia('https://medio.test/a', fuente, self.nota(), False)
        guardar.registrar_noticia('https://medio.test/a', fuente, self.nota(), True)
        self.assertEqual(Noticia.objects.count(), 1)
        self.assertEqual(Noticia.objects.get().estado_procesamiento, Noticia.EstadoProcesamiento.PENDIENTE)

    def test_registrar_noticia_sin_titulo_usa_la_url(self):
        noticia = guardar.registrar_noticia('https://medio.test/a', crear_fuente(), self.nota(titulo=''), True)
        self.assertEqual(noticia.titulo, 'https://medio.test/a')

    def test_cerrar_noticia(self):
        noticia = crear_noticia()
        guardar.cerrar_noticia(noticia, guardar.Estado.PROCESADA)
        noticia.refresh_from_db()
        self.assertEqual(noticia.estado_procesamiento, 'PROCESADA')

    def test_urls_ya_procesadas_solo_incluye_procesadas_y_descartadas(self):
        fuente = crear_fuente()
        for i, estado in enumerate(('PENDIENTE', 'PROCESADA', 'DESCARTADA', 'ERROR')):
            crear_noticia(fuente, f'https://medio.test/{estado}', estado_procesamiento=estado)
        self.assertEqual(guardar.urls_ya_procesadas(),
                         {'https://medio.test/PROCESADA', 'https://medio.test/DESCARTADA'})

    def test_urls_ya_procesadas_sin_base_de_datos_usa_el_archivo(self):
        with mock.patch.object(config, 'GUARDAR_EN_BD', False), \
                mock.patch.object(guardar, 'cargar_procesadas', return_value={'u'}):
            self.assertEqual(guardar.urls_ya_procesadas(), {'u'})

    # --- catálogo de especies ---------------------------------------------------
    def test_buscar_especie_por_cientifico_y_por_nombre_comun(self):
        onca = crear_especie('Panthera onca', 'Yaguareté, Jaguar')
        crear_especie('Tapirus terrestris', 'Tapir o Anta')
        self.assertEqual(guardar.buscar_especie(['Panthera onca'], 'cualquiera'), onca)
        self.assertEqual(guardar.buscar_especie([], 'JAGUAR'), onca)
        self.assertEqual(guardar.buscar_especie([], 'anta').nombre_cientifico, 'Tapirus terrestris')

    def test_buscar_especie_por_nombre_contenido(self):
        onca = crear_especie('Panthera onca', 'Yaguareté')
        self.assertEqual(guardar.buscar_especie([], 'yaguareté overo'), onca)

    def test_buscar_especie_desconocida_o_vacia(self):
        crear_especie('Panthera onca', 'Yaguareté')
        self.assertIsNone(guardar.buscar_especie(['Felis catus'], 'gato doméstico'))
        self.assertIsNone(guardar.buscar_especie([], ''))

    def test_buscar_especie_ambigua_devuelve_none(self):
        crear_especie('Leopardus pardalis', 'Gato montés')
        crear_especie('Leopardus geoffroyi', 'Gato montés')
        self.assertIsNone(guardar.buscar_especie([], 'gato montés'))

    def test_es_duplicado(self):
        esp = crear_especie()
        ub = crear_ubicacion(latitud=-25.6, longitud=-54.5)
        crear_registro(especie=esp, ubicacion=ub, fecha_avistamiento=date(2026, 5, 3))
        self.assertTrue(guardar.es_duplicado(esp, date(2026, 5, 3), self.geo()))
        self.assertFalse(guardar.es_duplicado(esp, date(2026, 5, 4), self.geo()))
        self.assertFalse(guardar.es_duplicado(esp, date(2026, 5, 3), self.geo(lat=-27.0, lon=-55.0)))

    def test_es_duplicado_sin_datos_suficientes(self):
        esp = crear_especie()
        self.assertFalse(guardar.es_duplicado(None, date(2026, 5, 3), self.geo()))
        self.assertFalse(guardar.es_duplicado(esp, None, self.geo()))
        self.assertFalse(guardar.es_duplicado(esp, date(2026, 5, 3), None))

    # --- registros ---------------------------------------------------------------
    def test_guardar_avistamiento_crea_registro_pendiente_con_extraccion(self):
        noticia = crear_noticia()
        esp = crear_especie()
        reg = guardar.guardar_avistamiento(noticia, self.av(), esp, self.geo(), 0.78504, {'cita': 'x'})
        reg.refresh_from_db()
        self.assertEqual(reg.estado_calidad, Registro.EstadoCalidad.PENDIENTE)
        self.assertEqual(reg.especie, esp)
        self.assertEqual(reg.noticia, noticia)
        self.assertEqual(reg.fragmento_origen, 'Un yaguareté fue visto')
        self.assertIsNone(reg.usuario_carga)
        self.assertEqual(reg.ubicacion.nombre_lugar, 'Puerto Iguazú')
        self.assertEqual(reg.ubicacion.departamento, 'Iguazú')
        self.assertEqual(reg.ubicacion.precision, config.PRECISION_UBICACION)
        extraccion = reg.extraccion
        self.assertEqual(extraccion.confianza, Decimal('0.7850'))
        self.assertEqual(extraccion.json_extraido, {'cita': 'x'})
        self.assertEqual(extraccion.version_prompt, config.PROMPT_VERSION)
        self.assertEqual(extraccion.estado_revision, ExtraccionIA.EstadoRevision.PENDIENTE)

    def test_guardar_avistamiento_sin_especie_ni_ubicacion(self):
        reg = guardar.guardar_avistamiento(crear_noticia(), self.av(), None, None, 0.5, {'a': 1})
        self.assertIsNone(reg.especie)
        self.assertIsNone(reg.ubicacion)

    def test_guardar_avistamiento_es_todo_o_nada(self):
        with self.assertRaises(ValidationError):
            # tipo de confianza fuera de rango: la extracción es inválida y debe revertir el registro
            guardar.guardar_avistamiento(crear_noticia(), self.av(), None, None, 5.0, {'a': 1})
        self.assertEqual(Registro.objects.count(), 0)

    def test_las_ubicaciones_iguales_se_reutilizan(self):
        noticia = crear_noticia()
        guardar.guardar_avistamiento(noticia, self.av(), None, self.geo(), 0.5, {'a': 1})
        guardar.guardar_avistamiento(noticia, self.av(), None, self.geo(), 0.5, {'a': 1})
        self.assertEqual(Ubicacion.objects.count(), 1)
        self.assertEqual(Registro.objects.count(), 2)

    def test_ubicacion_sin_zona_usa_el_departamento_o_misiones_como_nombre(self):
        ub1 = guardar._obtener_ubicacion(self.geo(zona='', depto='Oberá'))
        ub2 = guardar._obtener_ubicacion(self.geo(lat=-26.0, lon=-54.0, zona='', depto=''))
        self.assertEqual(ub1.nombre_lugar, 'Oberá')
        self.assertEqual(ub2.nombre_lugar, 'Misiones')

    # --- CSV de auditoría ---------------------------------------------------------
    def test_registrar_csv_escribe_encabezado_una_sola_vez(self):
        with tempfile.TemporaryDirectory() as tmp:
            archivo = Path(tmp) / 'log.csv'
            with mock.patch.object(config, 'LOG_CSV', archivo):
                guardar.registrar_csv({'url': 'https://m/1', 'titulo': 'Uno', 'resultado': 'guardado', 'ignorado': 'x'})
                guardar.registrar_csv({'url': 'https://m/2', 'titulo': 'Dos', 'resultado': 'descartado'})
            lineas = archivo.read_text(encoding='utf-8').strip().splitlines()
        self.assertEqual(len(lineas), 3)
        self.assertTrue(lineas[0].startswith('url,titulo'))
        self.assertIn('https://m/1', lineas[1])
        self.assertNotIn('ignorado', lineas[0])