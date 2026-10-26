from django import forms
from django.contrib.auth.forms import UserCreationForm

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
        widget=forms.TextInput(attrs={'placeholder': 'Ej. Parque Nacional Iguazú'}),
    )
    lugar_zona = forms.CharField(
        label='Zona', max_length=100, required=False,
        widget=forms.TextInput(attrs={'placeholder': 'Ej. Selva paranaense'}),
    )
    lugar_latitud = forms.FloatField(
        label='Latitud', required=False, min_value=-90, max_value=90,
        widget=forms.NumberInput(attrs={'step': 'any', 'placeholder': '-26.916700'}),
        help_text='Entre -90 y 90. Misiones ronda -25 a -28.',
    )
    lugar_longitud = forms.FloatField(
        label='Longitud', required=False, min_value=-180, max_value=180,
        widget=forms.NumberInput(attrs={'step': 'any', 'placeholder': '-54.500000'}),
        help_text='Entre -180 y 180. Misiones ronda -53 a -56.',
    )
    lugar_precision = forms.ChoiceField(
        label='Precisión de la ubicación', required=False,
        choices=Ubicacion.Precision.choices, initial=Ubicacion.Precision.APROXIMADA,
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
            'especie': 'Dejala vacía si todavía no se pudo identificar.',
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
        'lugar_nombre', 'lugar_zona', 'lugar_latitud', 'lugar_longitud', 'lugar_precision',
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

        self.es_manual = self.instance.origen_registro == Registro.OrigenRegistro.MANUAL

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
                self.initial.setdefault('lugar_zona', ubicacion.zona)
                self.initial.setdefault('lugar_latitud', ubicacion.latitud)
                self.initial.setdefault('lugar_longitud', ubicacion.longitud)
                self.initial.setdefault('lugar_precision', ubicacion.precision)

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
        if any([cleaned.get('lugar_nombre'), cleaned.get('lugar_zona'), lat is not None, lon is not None]):
            if not cleaned.get('lugar_nombre') and 'lugar_nombre' not in self.errors:
                self.add_error('lugar_nombre', 'Indicá el lugar.')
            if lat is None and 'lugar_latitud' not in self.errors:
                self.add_error('lugar_latitud', 'Indicá la latitud.')
            if lon is None and 'lugar_longitud' not in self.errors:
                self.add_error('lugar_longitud', 'Indicá la longitud.')
        return cleaned

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
        datos = {
            'nombre_lugar': _limpiar_espacios(cd['lugar_nombre']),
            'zona': _limpiar_espacios(cd.get('lugar_zona')),
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