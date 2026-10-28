from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    Auditoria,
    ExtraccionIA,
    Especie,
    Fuente,
    Noticia,
    Registro,
    Rol,
    Ubicacion,
    Usuario,
)


@admin.register(Rol)
class RolAdmin(admin.ModelAdmin):
    list_display = ('id_rol', 'nombre')


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    list_display = ('username', 'email', 'rol', 'is_active', 'is_staff')
    list_filter = ('rol', 'is_active', 'is_staff')
    fieldsets = UserAdmin.fieldsets + (('Rol en el sistema', {'fields': ('rol',)}),)
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Datos adicionales', {'fields': ('email', 'rol')}),
    )


@admin.register(Fuente)
class FuenteAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'url_base', 'activa')
    list_filter = ('activa',)
    search_fields = ('nombre', 'url_base')


@admin.register(Noticia)
class NoticiaAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'fuente', 'fecha_publicacion', 'origen', 'estado_procesamiento')
    list_filter = ('estado_procesamiento', 'origen', 'fuente')
    search_fields = ('titulo', 'url')
    date_hierarchy = 'fecha_publicacion'


@admin.register(Especie)
class EspecieAdmin(admin.ModelAdmin):
    list_display = ('nombre_cientifico', 'nombre_comun', 'clase_taxonomica', 'estado_conservacion')
    list_filter = ('clase_taxonomica', 'estado_conservacion')
    search_fields = ('nombre_cientifico', 'nombre_comun')


@admin.register(Ubicacion)
class UbicacionAdmin(admin.ModelAdmin):
    list_display = ('nombre_lugar', 'zona', 'latitud', 'longitud', 'precision')
    list_filter = ('zona', 'precision')
    search_fields = ('nombre_lugar', 'zona')


@admin.register(Registro)
class RegistroAdmin(admin.ModelAdmin):
    list_display = ('id_registro', 'especie', 'ubicacion', 'fecha_avistamiento', 'tipo_registro', 'estado_calidad')
    list_filter = ('estado_calidad', 'tipo_registro')
    search_fields = ('especie__nombre_cientifico', 'especie__nombre_comun', 'noticia__titulo')
    raw_id_fields = ('noticia', 'especie', 'ubicacion')


@admin.register(ExtraccionIA)
class ExtraccionIAAdmin(admin.ModelAdmin):
    list_display = ('id_extraccion', 'registro', 'modelo', 'confianza', 'estado_revision', 'fecha_extraccion')
    list_filter = ('estado_revision', 'modelo')
    raw_id_fields = ('registro',)


@admin.register(Auditoria)
class AuditoriaAdmin(admin.ModelAdmin):
    """Solo lectura: la tabla la llenan los triggers de la base de datos."""
    list_display = ('fecha_hora', 'tabla_afectada', 'id_registro_afectado', 'operacion', 'usuario_bd')
    list_filter = ('tabla_afectada', 'operacion')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False