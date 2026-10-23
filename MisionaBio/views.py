# MisionaBio/views.py
from datetime import date, datetime, timedelta
from functools import wraps
from django.contrib.auth import login
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, ProtectedError, Q
from django.db.models.functions import TruncMonth
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.timesince import timesince
from django.views.decorators.http import require_GET

from .forms import (
    AvistamientoFiltroForm,
    AvistamientoForm,
    EspecieFiltroForm,
    EspecieForm,
    ImagenFormSet,
    RegistroUsuarioForm
)
from .models import Especie, Fuente, Registro


# =============================================================================
# Utilidades
# =============================================================================
def _es_admin(user):
    return user.is_authenticated and (user.is_superuser or user.es_administrador())

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


# -----------------------------------------------------------------------------
# Dashboard
# -----------------------------------------------------------------------------

# Bounding box aproximado de Misiones: descarta coordenadas mal geocodificadas del scraping.
MISIONES_LAT = (-28.3, -25.4)
MISIONES_LON = (-56.1, -53.6)
MAX_REGISTROS_MAPA = 5000   # tope de puntos que se envían al navegador
MESES_SERIE = 36            # meses de historia para el gráfico por mes


def _kpis():
    """Números de las tarjetas. Los usa la página y el endpoint de actualización en vivo."""
    ahora = timezone.now()
    desde = ahora - timedelta(hours=24)
    registros = Registro.objects
    return {
        'especies': Especie.objects.count(),
        'especies_con_avistamientos': (
            registros.filter(especie__isnull=False)
            .aggregate(n=Count('especie', distinct=True))['n']
        ),
        'avistamientos': registros.count(),
        # "Últimas 24 hs" = registros que se cargaron al sistema en ese lapso (fecha_carga),
        # no los que ocurrieron en ese lapso: fecha_avistamiento es solo una fecha, sin hora.
        'ultimas_24h': registros.filter(fecha_carga__gte=desde).count(),
        'pendientes': registros.filter(estado_calidad=Registro.EstadoCalidad.PENDIENTE).count(),
        'actualizado': timezone.localtime(ahora).strftime('%H:%M:%S'),
    }


def _serie_mensual(hoy, cantidad=MESES_SERIE):
    """Avistamientos por mes (según fecha_avistamiento), con ceros en los meses vacíos."""
    base = hoy.year * 12 + (hoy.month - 1) - (cantidad - 1)
    meses = [((base + i) // 12, (base + i) % 12 + 1) for i in range(cantidad)]
    inicio = date(meses[0][0], meses[0][1], 1)

    filas = (
        Registro.objects
        .filter(fecha_avistamiento__gte=inicio)
        .annotate(mes=TruncMonth('fecha_avistamiento'))
        .values('mes')
        .annotate(n=Count('id_registro'))
        .order_by('mes')
    )
    conteo = {}
    for fila in filas:
        mes = fila['mes']
        if isinstance(mes, datetime):   # algunos motores devuelven datetime
            mes = mes.date()
        conteo[(mes.year, mes.month)] = fila['n']
    return [{'y': y, 'm': m, 'n': conteo.get((y, m), 0)} for y, m in meses]


def _estados():
    conteo = {
        f['estado_calidad']: f['n']
        for f in (
            Registro.objects.order_by()
            .values('estado_calidad')
            .annotate(n=Count('id_registro'))
        )
    }
    return [
        {'clave': clave, 'nombre': nombre, 'n': conteo.get(clave, 0)}
        for clave, nombre in Registro.EstadoCalidad.choices
    ]


def _clases(limite=8):
    filas = (
        Especie.objects.order_by()
        .values('clase_taxonomica')
        .annotate(n=Count('id_especie'))
        .order_by('-n', 'clase_taxonomica')[:limite]
    )
    # Oracle guarda el texto vacío como NULL: ambos casos van a "Sin clasificar".
    return [{'nombre': f['clase_taxonomica'] or 'Sin clasificar', 'n': f['n']} for f in filas]


def _top_especies(limite=6):
    filas = (
        Registro.objects.filter(especie__isnull=False).order_by()
        .values('especie__nombre_comun', 'especie__nombre_cientifico')
        .annotate(n=Count('id_registro'))
        .order_by('-n', 'especie__nombre_cientifico')[:limite]
    )
    return [
        {
            'nombre': f['especie__nombre_comun'] or f['especie__nombre_cientifico'],
            'cientifico': f['especie__nombre_cientifico'] if f['especie__nombre_comun'] else '',
            'n': f['n'],
        }
        for f in filas
    ]


def _registros_mapa():
    """
    Un registro por avistamiento: [longitud, latitud, zona, fecha ISO, especie].
    Formato de ECharts (longitud primero). El agrupado en celdas/puntos lo hace el navegador,
    así el filtro por especie del mapa no necesita pedirle nada al servidor.
    """
    filas = (
        Registro.objects
        .filter(latitud__range=MISIONES_LAT, longitud__range=MISIONES_LON)
        .order_by('-fecha_avistamiento', '-id_registro')
        .values_list(
            'longitud', 'latitud', 'zona', 'fecha_avistamiento',
            'especie__nombre_comun', 'especie__nombre_cientifico',
        )[:MAX_REGISTROS_MAPA]
    )
    return [
        [
            float(lon),   # float(): DecimalField no se serializa a JSON directamente
            float(lat),
            zona or '',
            fecha.isoformat(),
            comun or cientifico or 'Especie sin identificar',
        ]
        for lon, lat, zona, fecha, comun, cientifico in filas
    ]


def _ultimos_cargados(limite=6):
    registros = Registro.objects.select_related('especie').order_by('-fecha_carga', '-id_registro')[:limite]
    filas = []
    for r in registros:
        esp = r.especie
        filas.append({
            'url': reverse('avistamientos:detalle', args=[r.pk]),
            'especie': (esp.nombre_comun or esp.nombre_cientifico) if esp else 'Especie sin identificar',
            'cientifico': esp.nombre_cientifico if esp and esp.nombre_comun else '',
            'zona': r.zona,
            'fecha': r.fecha_avistamiento,
            'estado': r.estado_calidad,
            'estado_nombre': r.get_estado_calidad_display(),
            'hace': (
                'menos de un minuto'
                if (timezone.now() - r.fecha_carga).total_seconds() < 60
                else timesince(r.fecha_carga, depth=1)
            ),
        })
    return filas


@login_required
@require_GET
def dashboard(request):
    datos = {
        'mensual': _serie_mensual(timezone.localdate()),
        'estados': _estados(),
        'clases': _clases(),
        'top': _top_especies(),
        'registros': _registros_mapa(),
    }
    return render(request, 'MisionaBio/dashboard.html', {
        'kpis': _kpis(),
        'ultimos': _ultimos_cargados(),
        'datos': datos,   # el template lo pasa al navegador con json_script (escapado)
    })


@login_required
@require_GET
def dashboard_datos(request):
    """Números de las tarjetas en JSON: la página los consulta cada tanto para actualizarse sola."""
    return JsonResponse(_kpis())


# =============================================================================
# CRUD de Especies
# =============================================================================

@login_required
def especie_lista(request):
    form = EspecieFiltroForm(request.GET, label_suffix='')
    form.is_valid()  # los campos inválidos simplemente se ignoran
    cd = form.cleaned_data

    # Con annotate(Count) Django ignora Meta.ordering, así que se ordena explícitamente
    # (paginación estable).
    especies = (
        Especie.objects
        .annotate(num_avistamientos=Count('registros'))
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
    registros = especie.registros.select_related('fuente').order_by('-fecha_avistamiento', '-id_registro')
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

    registros = Registro.objects.select_related('especie', 'fuente')

    if cd.get('q'):
        q = cd['q'].strip()
        registros = registros.filter(
            Q(especie__nombre_cientifico__icontains=q) | Q(especie__nombre_comun__icontains=q)
        )
    if cd.get('fuente'):
        registros = registros.filter(fuente=cd['fuente'])
    if cd.get('estado'):
        registros = registros.filter(estado_calidad=cd['estado'])
    if cd.get('zona'):
        registros = registros.filter(zona__icontains=cd['zona'].strip())
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
        Registro.objects.select_related('especie', 'fuente', 'usuario_curador').prefetch_related('imagenes'),
        pk=pk,
    )
    return render(request, 'MisionaBio/avistamientos/detalle.html', {'registro': registro})


def _guardar_avistamiento(request, form):
    """Guarda el registro y deja constancia del curador cuando se valida/corrige (RNF10)."""
    registro = form.save(commit=False)
    if registro.estado_calidad in (Registro.EstadoCalidad.VALIDADO, Registro.EstadoCalidad.CORREGIDO):
        registro.usuario_curador = request.user
    else:
        registro.usuario_curador = None
    registro.save()
    return registro


def _fuente_manual():
    """Fuente "Carga manual": la única que puede usar un Visitante."""
    fuente = Fuente.objects.filter(tipo=Fuente.Tipo.MANUAL).first()
    if fuente is None:
        fuente, _ = Fuente.objects.get_or_create(
            nombre='Carga manual', defaults={'tipo': Fuente.Tipo.MANUAL},
        )
    return fuente



@login_required
def avistamiento_crear(request):
    """Cualquier usuario con sesión puede cargar un avistamiento; solo el admin lo valida."""
    es_admin = _es_admin(request.user)
    initial = {}
    instance = None

    if es_admin:
        fuente_manual = Fuente.objects.filter(tipo=Fuente.Tipo.MANUAL).first()
        if fuente_manual:
            initial['fuente'] = fuente_manual
    else:
        # Fuente y estado quedan fijados desde el inicio (no son editables para el Visitante).
        instance = Registro(fuente=_fuente_manual(), estado_calidad=Registro.EstadoCalidad.PENDIENTE)

    # Permite llegar desde la ficha de una especie: /avistamientos/nuevo/?especie=3
    if request.GET.get('especie', '').isdigit():
        initial['especie'] = int(request.GET['especie'])

    form = AvistamientoForm(request.POST or None, instance=instance, initial=initial, es_admin=es_admin)
    formset = ImagenFormSet(request.POST or None, request.FILES or None, instance=instance)
    if request.method == 'POST':
        form_ok = form.is_valid()
        formset_ok = formset.is_valid()
        if form_ok and formset_ok:
            # Todo o nada: no queda un avistamiento guardado sin sus fotos si algo falla.
            with transaction.atomic():
                registro = _guardar_avistamiento(request, form)
                formset.instance = registro
                formset.save()
            if es_admin:
                messages.success(request, 'El avistamiento se registró correctamente.')
            else:
                messages.success(request, 'Gracias: tu avistamiento se registró y quedó pendiente de validación.')
            return redirect('avistamientos:detalle', pk=registro.pk)
    return render(request, 'MisionaBio/avistamientos/form.html', {
        'form': form,
        'formset': formset,
        'es_admin': es_admin,
        'titulo': 'Nuevo avistamiento',
        'boton': 'Registrar avistamiento',
    })


@solo_admin
def avistamiento_editar(request, pk):
    registro = get_object_or_404(Registro, pk=pk)
    form = AvistamientoForm(request.POST or None, instance=registro)
    formset = ImagenFormSet(request.POST or None, request.FILES or None, instance=registro)
    if request.method == 'POST':
        form_ok = form.is_valid()
        formset_ok = formset.is_valid()
        if form_ok and formset_ok:
            with transaction.atomic():
                registro = _guardar_avistamiento(request, form)
                formset.instance = registro
                formset.save()
            messages.success(request, 'El avistamiento se actualizó correctamente.')
            return redirect('avistamientos:detalle', pk=registro.pk)
    return render(request, 'MisionaBio/avistamientos/form.html', {
        'form': form,
        'formset': formset,
        'registro': registro,
        'es_admin': True,
        'titulo': 'Editar avistamiento',
        'boton': 'Guardar cambios',
    })


@solo_admin
def avistamiento_eliminar(request, pk):
    registro = get_object_or_404(Registro.objects.select_related('especie', 'fuente'), pk=pk)
    if request.method == 'POST':
        registro.delete()
        messages.success(request, 'El avistamiento se eliminó.')
        return redirect('avistamientos:lista')
    return render(request, 'MisionaBio/avistamientos/confirmar_eliminar.html', {'registro': registro})