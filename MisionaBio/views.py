# MisionaBio/views.py
from functools import wraps
from django.contrib.auth import login
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, IntegerField, OuterRef, ProtectedError, Q, Subquery
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .forms import RegistroUsuarioForm

from .forms import (
    AvistamientoFiltroForm,
    AvistamientoForm,
    EspecieFiltroForm,
    EspecieForm,
    RegistroUsuarioForm,
    RegistroAdminForm,
)
from .models import Especie, Registro


# =============================================================================
# Utilidades
# =============================================================================
def solo_admin(view):
    """
    Exige sesión iniciada Y rol Administrador (RNF05).
    El Visitante puede consultar (listado y ficha) pero no crear/editar/eliminar.
    """
    @login_required
    @wraps(view)
    def envoltura(request, *args, **kwargs):
        user = request.user
        if not (user.is_superuser or user.es_administrador()):
            messages.error(request, 'No tenés permisos para realizar esta acción.')
            return redirect('index')
        return view(request, *args, **kwargs)
    return envoltura


def _paginar(request, queryset, por_pagina):
    paginator = Paginator(queryset, por_pagina)
    page_obj = paginator.get_page(request.GET.get('page'))
    page_range = list(paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1))
    return page_obj, page_range


def _querystring_sin_pagina(request):
    """Conserva los filtros al cambiar de página."""
    params = request.GET.copy()
    params.pop('page', None)
    return params.urlencode()


def _por_pagina(cleaned):
    return int(cleaned.get('por_pagina') or 10)


# =============================================================================
# Páginas generales
# =============================================================================

def index(request):
    return render(request, 'MisionaBio/index.html')

def registro(request):
    """Alta de cuenta: se crea siempre con rol Visitante (ver RegistroUsuarioForm)."""
    if request.user.is_authenticated:
        return redirect('dashboard')
    form = RegistroUsuarioForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        usuario = form.save()
        login(request, usuario)
        messages.success(request, '¡Cuenta creada! Ya iniciaste sesión.')
        return redirect('index')
    return render(request, 'MisionaBio/registro.html', {'form': form})

@solo_admin
def registro_admin(request):
    """Un administrador da de alta a otro. No inicia sesión con la cuenta nueva."""
    form = RegistroAdminForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        usuario = form.save()
        messages.success(request, f'Administrador «{usuario.username}» creado correctamente.')
        return redirect('dashboard')
    return render(request, 'MisionaBio/registro_admin.html', {'form': form})


@login_required
def dashboard(request):
    return render(request, 'MisionaBio/dashboard.html')


# =============================================================================
# CRUD de Especies
# =============================================================================

@login_required
def especie_lista(request):
    form = EspecieFiltroForm(request.GET, label_suffix='')
    form.is_valid()  # los campos inválidos simplemente se ignoran
    cd = form.cleaned_data

        # El conteo va en una subconsulta y no con annotate(Count): un GROUP BY sobre Especie
    # incluiría la columna "descripcion" (NCLOB), que Oracle no permite agrupar (ORA-00932).
    # Se ordena explícitamente para que la paginación sea estable.
    avistamientos = (
        Registro.objects
        .filter(especie=OuterRef('pk'))
        .order_by()
        .values('especie')
        .annotate(n=Count('pk'))
        .values('n')
    )
    especies = (
        Especie.objects
        .annotate(num_avistamientos=Coalesce(Subquery(avistamientos, output_field=IntegerField()), 0))
        .order_by('nombre_cientifico')
    )

    if cd.get('q'):
        q = cd['q'].strip()
        especies = especies.filter(
            Q(nombre_cientifico__icontains=q) | Q(nombre_comun__icontains=q)
        )
    if cd.get('clase'):
        especies = especies.filter(clase_taxonomica=cd['clase'])
    if cd.get('estado'):
        especies = especies.filter(estado_conservacion=cd['estado'])

    page_obj, page_range = _paginar(request, especies, _por_pagina(cd))

    return render(request, 'MisionaBio/especies/lista.html', {
        'form': form,
        'page_obj': page_obj,
        'page_range': page_range,
        'total': page_obj.paginator.count,
        'hay_filtros': any(cd.get(k) for k in ('q', 'clase', 'estado')),
        'querystring': _querystring_sin_pagina(request),
    })


@login_required
def especie_detalle(request, pk):
    especie = get_object_or_404(Especie, pk=pk)
    registros = (
        especie.registros
        .select_related('noticia__fuente', 'ubicacion')
        .order_by(F('fecha_avistamiento').desc(nulls_last=True), '-id_registro')
    )
    return render(request, 'MisionaBio/especies/detalle.html', {
        'especie': especie,
        'registros': registros[:10],
        'total_registros': registros.count(),
    })


@solo_admin
def especie_crear(request):
    form = EspecieForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        especie = form.save()
        messages.success(request, f'La especie «{especie.nombre_cientifico}» se creó correctamente.')
        return redirect('especies:detalle', pk=especie.pk)
    return render(request, 'MisionaBio/especies/form.html', {
        'form': form,
        'titulo': 'Nueva especie',
        'boton': 'Crear especie',
    })


@solo_admin
def especie_editar(request, pk):
    especie = get_object_or_404(Especie, pk=pk)
    form = EspecieForm(request.POST or None, instance=especie)
    if request.method == 'POST' and form.is_valid():
        especie = form.save()
        messages.success(request, f'La especie «{especie.nombre_cientifico}» se actualizó correctamente.')
        return redirect('especies:detalle', pk=especie.pk)
    return render(request, 'MisionaBio/especies/form.html', {
        'form': form,
        'especie': especie,
        'titulo': 'Editar especie',
        'boton': 'Guardar cambios',
    })


@solo_admin
def especie_eliminar(request, pk):
    especie = get_object_or_404(Especie, pk=pk)
    total_registros = especie.registros.count()

    if request.method == 'POST':
        nombre = especie.nombre_cientifico
        try:
            especie.delete()
        except ProtectedError:
            # Registro.especie usa on_delete=PROTECT: no se pierden avistamientos por accidente.
            messages.error(
                request,
                f'No se puede eliminar «{nombre}» porque tiene avistamientos asociados.',
            )
            return redirect('especies:detalle', pk=pk)
        messages.success(request, f'La especie «{nombre}» se eliminó.')
        return redirect('especies:lista')

    return render(request, 'MisionaBio/especies/confirmar_eliminar.html', {
        'especie': especie,
        'total_registros': total_registros,
    })


# =============================================================================
# CRUD de Avistamientos (modelo Registro)
# =============================================================================

@login_required
def avistamiento_lista(request):
    form = AvistamientoFiltroForm(request.GET, label_suffix='')
    form.is_valid()
    cd = form.cleaned_data

    registros = Registro.objects.select_related('especie', 'ubicacion', 'noticia__fuente')

    if cd.get('q'):
        q = cd['q'].strip()
        registros = registros.filter(
            Q(especie__nombre_cientifico__icontains=q) | Q(especie__nombre_comun__icontains=q)
        )
    if cd.get('fuente'):
        registros = registros.filter(noticia__fuente=cd['fuente'])
    if cd.get('estado'):
        registros = registros.filter(estado_calidad=cd['estado'])
    if cd.get('zona'):
        registros = registros.filter(ubicacion__zona__icontains=cd['zona'].strip())
    if cd.get('desde'):
        registros = registros.filter(fecha_avistamiento__gte=cd['desde'])
    if cd.get('hasta'):
        registros = registros.filter(fecha_avistamiento__lte=cd['hasta'])

    page_obj, page_range = _paginar(request, registros, _por_pagina(cd))

    return render(request, 'MisionaBio/avistamientos/lista.html', {
        'form': form,
        'page_obj': page_obj,
        'page_range': page_range,
        'total': page_obj.paginator.count,
        'hay_filtros': any(cd.get(k) for k in ('q', 'fuente', 'estado', 'zona', 'desde', 'hasta')),
        'querystring': _querystring_sin_pagina(request),
    })


@login_required
def avistamiento_detalle(request, pk):
    registro = get_object_or_404(
        Registro.objects.select_related(
            'especie', 'ubicacion', 'noticia__fuente',
            'usuario_carga', 'usuario_validador', 'extraccion',
        ),
        pk=pk,
    )
    return render(request, 'MisionaBio/avistamientos/detalle.html', {'registro': registro})


@transaction.atomic
def _guardar_avistamiento(request, form):
    """
    Guarda el avistamiento con su noticia y su ubicación (todo o nada) y deja
    constancia de quién lo cargó y quién lo validó (RNF10).
    """
    registro = form.save(commit=False)
    es_nuevo = registro.pk is None

    registro.noticia = form.resolver_noticia()
    registro.ubicacion = form.resolver_ubicacion()

    if es_nuevo:
        registro.usuario_carga = request.user

    if registro.estado_calidad == Registro.EstadoCalidad.PENDIENTE:
        registro.usuario_validador = None
        registro.fecha_validacion = None
    elif 'estado_calidad' in form.changed_data or not registro.usuario_validador_id:
        # Validado, corregido o descartado: queda el responsable de la decisión.
        registro.usuario_validador = request.user
        registro.fecha_validacion = timezone.now()

    registro.save()
    return registro


@solo_admin
def avistamiento_crear(request):
    """
    Carga manual: solo el Administrador. El registro nace VALIDADO y sin extracción
    de IA (los avistamientos que llegan por scraping se revisan en ExtraccionIA).
    """
    initial = {'estado_calidad': Registro.EstadoCalidad.VALIDADO}

    # Permite llegar desde la ficha de una especie: /avistamientos/nuevo/?especie=3
    if request.GET.get('especie', '').isdigit():
        initial['especie'] = int(request.GET['especie'])

    form = AvistamientoForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        registro = _guardar_avistamiento(request, form)
        messages.success(request, 'El avistamiento se registró correctamente.')
        return redirect('avistamientos:detalle', pk=registro.pk)
    return render(request, 'MisionaBio/avistamientos/form.html', {
        'form': form,
        'es_admin': True,
        'titulo': 'Nuevo avistamiento',
        'boton': 'Registrar avistamiento',
    })


@solo_admin
def avistamiento_editar(request, pk):
    registro = get_object_or_404(
        Registro.objects.select_related('noticia__fuente', 'ubicacion'), pk=pk,
    )
    form = AvistamientoForm(request.POST or None, instance=registro)
    if request.method == 'POST' and form.is_valid():
        registro = _guardar_avistamiento(request, form)
        messages.success(request, 'El avistamiento se actualizó correctamente.')
        return redirect('avistamientos:detalle', pk=registro.pk)
    return render(request, 'MisionaBio/avistamientos/form.html', {
        'form': form,
        'registro': registro,
        'es_admin': True,
        'titulo': 'Editar avistamiento',
        'boton': 'Guardar cambios',
    })


@solo_admin
def avistamiento_eliminar(request, pk):
    registro = get_object_or_404(
        Registro.objects.select_related('especie', 'noticia__fuente'), pk=pk,
    )
    if request.method == 'POST':
        registro.delete()
        messages.success(request, 'El avistamiento se eliminó.')
        return redirect('avistamientos:lista')
    return render(request, 'MisionaBio/avistamientos/confirmar_eliminar.html', {'registro': registro})
def registro(request):
    if request.method == 'POST':
        form = RegistroUsuarioForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Cuenta creada correctamente. Ya podés iniciar sesión.')
            return redirect('login')
    else:
        form = RegistroUsuarioForm()
    return render(request, 'MisionaBio/registro.html', {'form': form})
