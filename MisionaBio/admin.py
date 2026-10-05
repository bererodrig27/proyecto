from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Usuario, Rol
from .models import Fuente, Especie, Registro, Imagen


@admin.register(Rol)
class RolAdmin(admin.ModelAdmin):
    list_display = ('id_rol', 'nombre')


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    list_display = ('username', 'email', 'rol', 'is_active', 'fecha_alta')
    list_filter = ('rol', 'is_active')
    fieldsets = UserAdmin.fieldsets + (
        ('Datos MisionaBio', {'fields': ('rol',)}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Datos MisionaBio', {'fields': ('rol', 'email')}),
    )


@admin.register(Fuente)
class FuenteAdmin(admin.ModelAdmin):
    list_display = ('id_fuente', 'nombre', 'tipo', 'confiabilidad')
    list_filter = ('tipo', 'confiabilidad')


@admin.register(Especie)
class EspecieAdmin(admin.ModelAdmin):
    list_display = ('id_especie', 'nombre_cientifico', 'nombre_comun', 'clase_taxonomica', 'estado_conservacion')
    list_filter = ('clase_taxonomica', 'estado_conservacion')
    search_fields = ('nombre_cientifico', 'nombre_comun')


class ImagenInline(admin.TabularInline):
    model = Imagen
    extra = 1


@admin.register(Registro)
class RegistroAdmin(admin.ModelAdmin):
    list_display = ('id_registro', 'especie', 'zona', 'fecha_avistamiento', 'estado_calidad', 'usuario_curador')
    list_filter = ('estado_calidad', 'zona', 'fecha_avistamiento')
    search_fields = ('especie__nombre_cientifico', 'especie__nombre_comun', 'zona')
    inlines = [ImagenInline]