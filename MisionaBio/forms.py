from datetime import timedelta

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.utils import timezone

from .geo import centro_departamento, centros_departamentos, departamento_de, hay_datos as hay_geo_departamentos
from .models import (
    Especie,
    Fuente,
    Noticia,
    Registro,
    Rol,
    Ubicacion,
    Usuario,
    _limpiar_espacios,
)

# =============================================================================
# Usuarios (ya existente)
# =============================================================================

class RegistroUsuarioForm(UserCreationForm):
    email = forms.EmailField(required=True, label="Correo electrónico")

    class Meta(UserCreationForm.Meta):
        model = Usuario
        fields = ('username', 'email')

    def save(self, commit=True):
        usuario = super().save(commit=False)
        usuario.email = self.cleaned_data['email']
        rol_visitante, _ = Rol.objects.get_or_create(nombre='Visitante')
        usuario.rol = rol_visitante
        if commit:
            usuario.save()
        return usuario

class RegistroAdminForm(RegistroUsuarioForm):
    """Alta de administradores. Solo se usa desde una vista protegida por solo_admin."""

    def save(self, commit=True):
        usuario = super().save(commit=False)   # el padre le pone Visitante...
        usuario.rol = Rol.objects.get_or_create(nombre=Rol.ADMINISTRADOR)[0]  # ...y acá lo pisamos
        if commit:
            usuario.save()
        return usuario


# =============================================================================
# Especies
# =============================================================================

POR_PAGINA_CHOICES = [('10', '10'), ('25', '25'), ('50', '50')]


class EspecieForm(forms.ModelForm):
    """Alta y edición. La normalización (RF02) la hace el propio modelo en clean()/save()."""

    class Meta:
        model = Especie
        fields = [
            'nombre_cientifico',
            'nombre_comun',
            'clase_taxonomica',
            'estado_conservacion',
            'taxonomia',
            'descripcion',   # al final: ocupa todo el ancho del formulario
        ]
        widgets = {
            'nombre_cientifico': forms.TextInput(attrs={'placeholder': 'Panthera onca'}),
            'nombre_comun': forms.TextInput(attrs={'placeholder': 'Yaguareté'}),
            'clase_taxonomica': forms.TextInput(attrs={'placeholder': 'Mammalia', 'list': 'clases-sugeridas'}),
            'taxonomia': forms.TextInput(attrs={'placeholder': 'Animalia > Chordata > Mammalia > Carnivora > Felidae'}),
            'descripcion': forms.Textarea(attrs={'rows': 4}),
        }


class EspecieFiltroForm(forms.Form):
    q = forms.CharField(
        label='Buscar', required=False,
        widget=forms.TextInput(attrs={'placeholder': 'Nombre científico o común'}),
    )
    clase = forms.ChoiceField(label='Clase taxonómica', required=False)
    estado = forms.ChoiceField(
        label='Estado de conservación', required=False,
        choices=[('', 'Todos')] + list(Especie.EstadoConservacion.choices),
    )
    por_pagina = forms.ChoiceField(label='Por página', required=False, choices=POR_PAGINA_CHOICES)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Las clases disponibles salen de lo que realmente hay cargado.
        clases = (
            Especie.objects
            .exclude(clase_taxonomica__isnull=True)
            .exclude(clase_taxonomica='')
            .order_by('clase_taxonomica')
            .values_list('clase_taxonomica', flat=True)
            .distinct()
        )
        self.fields['clase'].choices = [('', 'Todas')] + [(c, c) for c in clases]


# =============================================================================
# Avistamientos (modelo Registro)
# =============================================================================

# Antigüedad máxima (en días) de la fecha de un avistamiento cargado manualmente: ~2 meses.
MAX_DIAS_ATRAS_MANUAL = 60

# Bounding box aproximado de Misiones. Lo usa el formulario para validar coordenadas y el
# dashboard (views.py) para descartar puntos mal geocodificados: tienen que coincidir, por
# eso se definen una sola vez, acá.
MISIONES_LAT = (-28.3, -25.4)
MISIONES_LON = (-56.1, -53.6)


def _en_rango(valor, rango):
    return rango[0] <= valor <= rango[1]


class AvistamientoForm(forms.ModelForm):
    """
    Carga/edición manual de un avistamiento (solo Administrador).

    Un avistamiento (Registro) puede tener dos procedencias:
      - MANUAL (carga nueva desde este formulario): no necesita noticia. Se respalda
        con "observaciones" y con el usuario que lo carga. La URL de una noticia es
        solo un enlace de referencia opcional.
      - SCRAPING (registro generado por el scraping que el Administrador edita acá):
        conserva su noticia, que sigue siendo obligatoria.

    El origen NO es un campo del formulario: se fija en __init__ (los registros
    nuevos son MANUAL; los existentes conservan el suyo).

    El formulario expone resolver_noticia() / resolver_ubicacion(), que la vista
    llama dentro de una transacción para crear o reutilizar esas filas.
    resolver_noticia() devuelve None si no se indicó ninguna URL.
    """

    # --- Noticia de referencia (opcional en cargas manuales) ---
    noticia_url = forms.URLField(
        label='Enlace de referencia (opcional)', max_length=500, required=False,
        widget=forms.URLInput(attrs={'placeholder': 'https://…'}),
        help_text=(
            'Si el avistamiento salió en una noticia, pegá su URL. Si la noticia ya está '
            'cargada, se reutiliza y no hace falta completar título ni medio.'
        ),
    )
    noticia_titulo = forms.CharField(label='Título de la noticia', max_length=300, required=False)
    noticia_fuente = forms.ModelChoiceField(
        label='Medio', required=False, queryset=Fuente.objects.order_by('nombre'),
        empty_label='Seleccioná un medio',
    )
    noticia_fecha_publicacion = forms.DateField(
        label='Fecha de publicación', required=False,
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
    )

    # --- Ubicación (opcional: se deja vacía si no se pudo ubicar el lugar) ---
    lugar_nombre = forms.CharField(
        label='Lugar', max_length=200, required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'Ej. Parque Nacional Iguazú',
            'list': 'lista-lugares',      # <datalist> que arma el JS con los lugares ya cargados
            'autocomplete': 'off',
        }),
        help_text='Empezá a escribir: si el lugar ya está cargado se completan solos el departamento y las coordenadas.',
    )
    lugar_departamento = forms.ChoiceField(
        label='Departamento', required=False,
        choices=[('', 'Sin determinar')] + list(Ubicacion.Departamento.choices),
        help_text='Las coordenadas se completan solas con un punto aproximado del departamento.',
    )
    lugar_latitud = forms.FloatField(
        label='Latitud', required=False, min_value=-90, max_value=90,
        widget=forms.HiddenInput(),   # oculta: se completa sola desde el departamento
    )
    lugar_longitud = forms.FloatField(
        label='Longitud', required=False, min_value=-180, max_value=180,
        widget=forms.HiddenInput(),
    )
    lugar_precision = forms.ChoiceField(
        label='Precisión de la ubicación', required=False,
        choices=Ubicacion.Precision.choices, initial=Ubicacion.Precision.APROXIMADA,
        widget=forms.HiddenInput(),
    )

    class Meta:
        model = Registro
        fields = [
            'especie',
            'fecha_avistamiento',
            'tipo_registro',
            'estado_calidad',
            'observaciones',
            'fragmento_origen',
        ]
        widgets = {
            'fecha_avistamiento': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'tipo_registro': forms.TextInput(attrs={'placeholder': 'Ej. avistamiento directo, rastro, cámara trampa'}),
            'observaciones': forms.Textarea(attrs={
                'rows': 4,
                'placeholder': 'Quién lo observó, cómo se tuvo conocimiento, circunstancias…',
            }),
            'fragmento_origen': forms.Textarea(attrs={'rows': 3}),
        }
        help_texts = {
            'especie': 'Obligatoria si el estado es Validado o Corregido. Dejala vacía solo si queda Pendiente o Descartado.',
            'fecha_avistamiento': 'Dejala vacía si no se conoce con precisión.',
            'observaciones': (
                'Obligatorio en cargas manuales: es el respaldo del avistamiento '
                '(quién lo vio, cómo se supo, circunstancias).'
            ),
            'fragmento_origen': 'Texto de la noticia que respalda el avistamiento (si hay noticia).',
        }

    # Orden de aparición: avistamiento, respaldo (observaciones), noticia de
    # referencia, ubicación y, al final, el fragmento (textarea de ancho completo).
    field_order = [
        'especie', 'fecha_avistamiento', 'tipo_registro', 'estado_calidad',
        'observaciones',
        'noticia_url', 'noticia_titulo', 'noticia_fuente', 'noticia_fecha_publicacion',
        'lugar_nombre', 'lugar_departamento', 'lugar_latitud', 'lugar_longitud', 'lugar_precision',
        'fragmento_origen',
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['especie'].queryset = Especie.objects.order_by('nombre_cientifico')
        self.fields['especie'].empty_label = 'Sin identificar'

        # El origen lo decide el servidor, no el usuario: todo registro nuevo creado
        # desde este formulario es una carga manual. Se fija ANTES de validar para que
        # Registro.clean() aplique las reglas de procedencia correctas.
        if not self.instance.pk:
            self.instance.origen_registro = Registro.OrigenRegistro.MANUAL
            # Las cargas manuales del Administrador nacen validadas.
            self.initial.setdefault('estado_calidad', Registro.EstadoCalidad.VALIDADO)

        self.coordenadas_automaticas = False
        self.es_manual = self.instance.origen_registro == Registro.OrigenRegistro.MANUAL

        # Cargas manuales: la fecha solo puede ser de los últimos 2 meses. El min/max
        # del selector es una ayuda visual; la regla real se valida en el servidor.
        if self.es_manual:
            hoy = timezone.localdate()
            campo_fecha = self.fields['fecha_avistamiento']
            campo_fecha.widget.attrs['max'] = hoy.isoformat()
            campo_fecha.widget.attrs['min'] = (hoy - timedelta(days=MAX_DIAS_ATRAS_MANUAL)).isoformat()
            campo_fecha.help_text = (
                'Solo se aceptan fechas de los últimos 2 meses. '
                'Dejala vacía si no se conoce con precisión.'
            )

        # --- Obligatoriedad visible (el asterisco lo dibuja _campo.html) ---
        # Siempre obligatoria en cargas manuales: es el respaldo del avistamiento. Se marca con un
        # atributo propio y no con required=True: esa regla ya la valida Registro.clean() y
        # con required=True el error saldría duplicado.
        if self.es_manual:
            self.fields['observaciones'].siempre_obligatorio = True
            self.fields['observaciones'].widget.attrs['aria-required'] = 'true'
        # Condicionales: el asterisco aparece o no según otros campos (lo actualiza el JS del
        # template; la regla real se valida en el servidor, en Registro.clean() y en clean()).
        self.fields['especie'].requerido_si = 'estado'        # si el estado es Validado/Corregido
        for nombre in ('lugar_nombre', 'lugar_departamento'):
            self.fields[nombre].requerido_si = 'ubicacion'    # si se cargó cualquier dato de ubicación

        # Latitud, longitud y precisión van ocultas: la persona indica lugar + departamento y las
        # coordenadas se completan solas con un punto aproximado del departamento.
        self.ubicacion_ayuda = (
            'Opcional. Indicá el lugar y el departamento; las coordenadas se completan solas '
            'con un punto aproximado.'
        )

        # En un registro de scraping la noticia sigue siendo obligatoria.
        if not self.es_manual:
            self.fields['noticia_url'].required = True
            self.fields['noticia_url'].label = 'URL de la noticia'
            self.fields['noticia_url'].help_text = (
                'Si la noticia ya está cargada, se reutiliza y no hace falta completar título ni medio.'
            )

        # Al editar, los campos de noticia y ubicación se precargan desde el registro.
        if self.instance.pk:
            if self.instance.noticia_id:
                noticia = self.instance.noticia
                self.initial.setdefault('noticia_url', noticia.url)
                self.initial.setdefault('noticia_titulo', noticia.titulo)
                self.initial.setdefault('noticia_fuente', noticia.fuente_id)
                self.initial.setdefault('noticia_fecha_publicacion', noticia.fecha_publicacion)
            ubicacion = self.instance.ubicacion
            if ubicacion:
                self.initial.setdefault('lugar_nombre', ubicacion.nombre_lugar)
                self.initial.setdefault('lugar_departamento', ubicacion.departamento)
                self.initial.setdefault('lugar_latitud', ubicacion.latitud)
                self.initial.setdefault('lugar_longitud', ubicacion.longitud)
                self.initial.setdefault('lugar_precision', ubicacion.precision)

    def clean_fecha_avistamiento(self):
        """
        En cargas manuales no se aceptan fechas futuras ni anteriores a ~2 meses.
        La regla no se aplica a los registros de scraping (vienen de noticias que
        pueden ser antiguas) ni cuando se edita un registro sin cambiar la fecha,
        para no impedir corregir otros datos de registros viejos.
        """
        fecha = self.cleaned_data.get('fecha_avistamiento')
        if not fecha or not self.es_manual:
            return fecha
        if self.instance.pk and fecha == self.instance.fecha_avistamiento:
            return fecha

        hoy = timezone.localdate()
        limite = hoy - timedelta(days=MAX_DIAS_ATRAS_MANUAL)
        if fecha > hoy:
            raise forms.ValidationError('La fecha del avistamiento no puede ser futura.')
        if fecha < limite:
            raise forms.ValidationError(
                f'Solo se pueden cargar avistamientos de los últimos 2 meses '
                f'(desde el {limite:%d/%m/%Y}).'
            )
        return fecha

    def clean(self):
        cleaned = super().clean()

        # Título y medio solo son obligatorios si se indicó una URL y esa noticia
        # todavía no existe. Sin URL (carga manual sin noticia) no se pide nada.
        url = cleaned.get('noticia_url')
        if url and not Noticia.objects.filter(url=url).exists():
            if not cleaned.get('noticia_titulo'):
                self.add_error('noticia_titulo', 'Obligatorio si la noticia todavía no está cargada.')
            if not cleaned.get('noticia_fuente'):
                self.add_error('noticia_fuente', 'Obligatorio si la noticia todavía no está cargada.')

        # La ubicación es opcional, pero si se carga algo tiene que quedar completa.
        lat, lon = cleaned.get('lugar_latitud'), cleaned.get('lugar_longitud')
        if any([cleaned.get('lugar_nombre'), cleaned.get('lugar_departamento'), lat is not None, lon is not None]):
            if not cleaned.get('lugar_nombre') and 'lugar_nombre' not in self.errors:
                self.add_error('lugar_nombre', 'Indicá el lugar.')

            # Al editar: si se cambió el departamento y las coordenadas siguen siendo las del lugar
            # anterior, se descartan para recalcularlas desde el nuevo departamento.
            ubic = self.instance.ubicacion if self.instance.pk else None
            depto = cleaned.get('lugar_departamento')
            if (ubic and depto and depto != ubic.departamento
                    and (lat, lon) == (ubic.latitud, ubic.longitud)):
                lat = lon = None

            # Sin coordenadas pero con departamento: se usa un punto central del departamento
            # y la ubicación queda como APROXIMADA.
            if lat is None and lon is None and depto:
                centro = centro_departamento(depto)
                if centro:
                    lat, lon = centro
                    cleaned['lugar_latitud'], cleaned['lugar_longitud'] = lat, lon
                    cleaned['lugar_precision'] = Ubicacion.Precision.APROXIMADA
                    self.coordenadas_automaticas = True

            if lat is None or lon is None:
                if 'lugar_departamento' not in self.errors:
                    self.add_error('lugar_departamento', 'Elegí el departamento.')
            elif not self._ubicacion_sin_cambios(lat, lon, None):
                self._validar_coordenadas_misiones(lat, lon)
            self._resolver_departamento(cleaned, lat, lon)

            # Si las coordenadas son justo el punto central de su departamento (las completó el
            # servidor o el JavaScript del formulario), la ubicación es aproximada aunque se
            # haya elegido otra precisión: no es una medición.
            if lat is not None and lon is not None and self._es_centro_de_departamento(cleaned, lat, lon):
                cleaned['lugar_precision'] = Ubicacion.Precision.APROXIMADA

            # Los campos ocultos no muestran errores: se trasladan al departamento.
            for oculto in ('lugar_latitud', 'lugar_longitud'):
                for mensaje in self.errors.get(oculto, []):
                    if mensaje not in self.errors.get('lugar_departamento', []):
                        self.add_error('lugar_departamento', mensaje)
        return cleaned

    @staticmethod
    def _es_centro_de_departamento(cleaned, lat, lon):
        centro = centro_departamento(cleaned.get('lugar_departamento'))
        return bool(centro) and abs(lat - centro[0]) < 1e-4 and abs(lon - centro[1]) < 1e-4

    def _resolver_departamento(self, cleaned, lat, lon):
        """
        El dashboard agrupa las ubicaciones por departamento, así que siempre tiene que quedar uno:
          - si no se eligió, se calcula a partir de las coordenadas (geo.py);
          - si se eligió y las coordenadas caen claramente en otro, se avisa (no se pisa en silencio);
          - si no hay forma de calcularlo (falta el archivo de departamentos o el punto cae
            lejos de la provincia), hay que elegirlo a mano.
        """
        depto = cleaned.get('lugar_departamento')
        errores_coords = 'lugar_latitud' in self.errors or 'lugar_longitud' in self.errors
        calculado, exacto = (None, False)
        if lat is not None and lon is not None and not errores_coords:
            calculado, exacto = departamento_de(lat, lon)

        if not depto:
            if calculado:
                cleaned['lugar_departamento'] = calculado
            elif not errores_coords and 'lugar_departamento' not in self.errors:
                self.add_error('lugar_departamento', 'Elegí el departamento.')
            return

        if calculado and exacto and calculado != depto and not self._ubicacion_sin_cambios(lat, lon, depto):
            self.add_error(
                'lugar_departamento',
                f'Las coordenadas caen en el departamento {calculado}, no en {depto}. '
                'Corregí el departamento o las coordenadas.',
            )

    def _ubicacion_sin_cambios(self, lat, lon, depto):
        """
        Al editar un registro viejo sin tocar la ubicación no se vuelven a exigir las reglas
        nuevas (rango y departamento). Con depto=None solo se comparan las coordenadas.
        """
        ubic = self.instance.ubicacion if self.instance.pk else None
        if not ubic or (lat, lon) != (ubic.latitud, ubic.longitud):
            return False
        return depto is None or depto == ubic.departamento

    def _validar_coordenadas_misiones(self, lat, lon):
        """
        Fuera de Misiones el avistamiento se guarda pero el dashboard lo descarta del mapa
        (bounding box), así que se frena acá con un mensaje que explique el error más común.
        """
        if 'lugar_latitud' in self.errors or 'lugar_longitud' in self.errors:
            return
        if _en_rango(lat, MISIONES_LAT) and _en_rango(lon, MISIONES_LON):
            return
        if _en_rango(lon, MISIONES_LAT) and _en_rango(lat, MISIONES_LON):
            self.add_error('lugar_latitud', 'Parecen estar invertidas: primero va la latitud (ej. -27.36) y después la longitud (ej. -55.90).')
            return
        for campo, valor, rango, nombre in (
            ('lugar_latitud', lat, MISIONES_LAT, 'latitud'),
            ('lugar_longitud', lon, MISIONES_LON, 'longitud'),
        ):
            if _en_rango(valor, rango):
                continue
            if valor > 0 and _en_rango(-valor, rango):
                self.add_error(campo, f'Falta el signo negativo: en Misiones la {nombre} es negativa.')
            else:
                self.add_error(
                    campo,
                    f'La {nombre} queda fuera de Misiones (debe estar entre {rango[0]} y {rango[1]}).',
                )

    # --- Estructura visual del formulario (la usa _campos_form.html) ---
    def secciones_campos(self):
        """Campos agrupados en secciones. Cada sección: titulo, ayuda, campos y, si es plegable, 'abierta'."""
        def seccion(titulo, ayuda, nombres, plegable=False):
            campos = [self[n] for n in nombres]
            abierta = (
                not plegable
                or not self.es_manual                               # en scraping la noticia es obligatoria
                or any(c.errors for c in campos)
                or any(c.value() for c in campos)
            )
            return {'titulo': titulo, 'ayuda': ayuda, 'campos': campos, 'plegable': plegable, 'abierta': abierta}

        return [
            seccion('Avistamiento', '', ['especie', 'fecha_avistamiento', 'tipo_registro', 'estado_calidad', 'observaciones']),
            seccion(
                'Ubicación',
                self.ubicacion_ayuda,
                ['lugar_nombre', 'lugar_departamento', 'lugar_latitud', 'lugar_longitud', 'lugar_precision'],
            ),
            seccion(
                'Noticia de origen' if not self.es_manual else 'Noticia de referencia (opcional)',
                'Pegá el enlace si el avistamiento salió en una noticia.' if self.es_manual else '',
                ['noticia_url', 'noticia_titulo', 'noticia_fuente', 'noticia_fecha_publicacion', 'fragmento_origen'],
                plegable=True,
            ),
        ]

    def centros_departamentos(self):
        """{departamento: [lat, lon]} para que el formulario complete las coordenadas aproximadas."""
        return {nombre: list(punto) for nombre, punto in centros_departamentos().items()}

    def lugares_existentes(self):
        """
        Lugares ya cargados (uno por nombre) para autocompletar departamento y coordenadas.
        Evita crear variantes del mismo lugar ('Parque Nacional Iguazu' vs 'Parque Nacional Iguazú').
        """
        vistos, lugares = set(), []
        filas = Ubicacion.objects.order_by('nombre_lugar', 'id_ubicacion').values(
            'nombre_lugar', 'departamento', 'latitud', 'longitud', 'precision',
        )[:3000]
        for f in filas:
            clave = f['nombre_lugar'].strip().casefold()
            if not clave or clave in vistos:
                continue
            vistos.add(clave)
            lugares.append({
                'n': f['nombre_lugar'], 'd': f['departamento'] or '',
                'lat': f['latitud'], 'lon': f['longitud'], 'p': f['precision'],
            })
        return lugares

    def resolver_noticia(self):
        """
        Devuelve la Noticia del avistamiento, o None si no se indicó URL
        (solo posible en cargas manuales). La URL es única:
        - Sin URL: devuelve None.
        - URL nueva: crea la noticia (origen MANUAL).
        - URL de la noticia actual del registro: se corrigen sus datos.
        - URL de otra noticia ya cargada: se reutiliza sin modificarla.
        """
        cd = self.cleaned_data
        url = cd.get('noticia_url')
        if not url:
            return None

        existente = Noticia.objects.filter(url=url).first()

        if existente is None:
            return Noticia.objects.create(
                fuente=cd['noticia_fuente'],
                url=url,
                titulo=_limpiar_espacios(cd['noticia_titulo']),
                fecha_publicacion=cd.get('noticia_fecha_publicacion'),
                origen=Noticia.Origen.MANUAL,
                estado_procesamiento=Noticia.EstadoProcesamiento.PROCESADA,
            )

        if self.instance.noticia_id == existente.pk:
            if cd.get('noticia_titulo'):
                existente.titulo = _limpiar_espacios(cd['noticia_titulo'])
            if cd.get('noticia_fuente'):
                existente.fuente = cd['noticia_fuente']
            existente.fecha_publicacion = cd.get('noticia_fecha_publicacion')
            existente.save()
        return existente

    def resolver_ubicacion(self):
        """Devuelve la Ubicacion (reutiliza una idéntica si existe) o None si no se cargó."""
        cd = self.cleaned_data
        # clean() garantiza que o está todo vacío o todo completo.
        if not cd.get('lugar_nombre'):
            return None
        nombre = _limpiar_espacios(cd['lugar_nombre'])
        datos = {
            'nombre_lugar': nombre,
            # "zona" se conserva por compatibilidad (mapa, dashboard, admin) y replica al lugar.
            'zona': nombre[:100],
            'departamento': cd.get('lugar_departamento') or '',
            'latitud': cd['lugar_latitud'],
            'longitud': cd['lugar_longitud'],
            'precision': cd.get('lugar_precision') or Ubicacion.Precision.APROXIMADA,
        }
        return Ubicacion.objects.filter(**datos).first() or Ubicacion.objects.create(**datos)

class AvistamientoFiltroForm(forms.Form):
    q = forms.CharField(
        label='Buscar especie', required=False,
        widget=forms.TextInput(attrs={'placeholder': 'Nombre científico o común'}),
    )
    fuente = forms.ModelChoiceField(
        label='Fuente', required=False, empty_label='Todas',
        queryset=Fuente.objects.all(),
    )
    origen = forms.ChoiceField(                      # <-- NUEVO
        label='Origen', required=False,
        choices=[('', 'Todos')] + list(Registro.OrigenRegistro.choices),
    )
    estado = forms.ChoiceField(
        label='Estado de calidad', required=False,
        choices=[('', 'Todos')] + list(Registro.EstadoCalidad.choices),
    )
    zona = forms.CharField(
        label='Zona', required=False,
        widget=forms.TextInput(attrs={'placeholder': 'Ej. Selva paranaense'}),
    )
    desde = forms.DateField(label='Desde', required=False, widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    hasta = forms.DateField(label='Hasta', required=False, widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    por_pagina = forms.ChoiceField(label='Por página', required=False, choices=POR_PAGINA_CHOICES)

    def clean(self):
        cleaned = super().clean()
        desde, hasta = cleaned.get('desde'), cleaned.get('hasta')
        if desde and hasta and desde > hasta:
            self.add_error('hasta', 'La fecha "hasta" no puede ser anterior a "desde".')
        return cleaned