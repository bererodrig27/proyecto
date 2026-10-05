from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import Especie, Fuente, Registro, Rol, Usuario, Imagen

from django.forms import inlineformset_factory

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
            'taxonomia',   # al final: ocupa todo el ancho del formulario
        ]
        widgets = {
            'nombre_cientifico': forms.TextInput(attrs={'placeholder': 'Panthera onca'}),
            'nombre_comun': forms.TextInput(attrs={'placeholder': 'Yaguareté'}),
            'clase_taxonomica': forms.TextInput(attrs={'placeholder': 'Mammalia', 'list': 'clases-sugeridas'}),
            'taxonomia': forms.TextInput(attrs={'placeholder': 'Animalia > Chordata > Mammalia > Carnivora > Felidae'}),
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
    class Meta:
        model = Registro
        fields = [
            'especie',
            'fuente',
            'fecha_avistamiento',
            'zona',
            'latitud',
            'longitud',
            'estado_calidad',
        ]
        widgets = {
            'fecha_avistamiento': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'latitud': forms.NumberInput(attrs={'step': '0.000001', 'placeholder': '-26.916700'}),
            'longitud': forms.NumberInput(attrs={'step': '0.000001', 'placeholder': '-54.500000'}),
            'zona': forms.TextInput(attrs={'placeholder': 'Ej. Selva paranaense'}),
        }
        help_texts = {
            'especie': 'Dejala vacía si todavía no se pudo identificar (queda pendiente de sugerencia).',
            'latitud': 'Entre -90 y 90. Misiones ronda -25 a -28.',
            'longitud': 'Entre -180 y 180. Misiones ronda -53 a -56.',
        }

    def __init__(self, *args, es_admin=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['especie'].queryset = Especie.objects.order_by('nombre_cientifico')
        self.fields['especie'].empty_label = 'Sin identificar'
        self.fields['fuente'].empty_label = 'Seleccioná una fuente'

        if not es_admin:
            # El Visitante no elige la fuente ni el estado de calidad: la vista los fija
            # (fuente "Carga manual", estado "Pendiente") para no falsear la procedencia.
            del self.fields['fuente']
            del self.fields['estado_calidad']
            self.order_fields(['especie', 'fecha_avistamiento', 'latitud', 'longitud', 'zona'])


class AvistamientoFiltroForm(forms.Form):
    q = forms.CharField(
        label='Buscar especie', required=False,
        widget=forms.TextInput(attrs={'placeholder': 'Nombre científico o común'}),
    )
    fuente = forms.ModelChoiceField(
        label='Fuente', required=False, empty_label='Todas',
        queryset=Fuente.objects.all(),
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


class ImagenForm(forms.ModelForm):
    class Meta:
        model = Imagen
        fields = ['archivo', 'descripcion']
        widgets = {
            'archivo': forms.ClearableFileInput(
                attrs={'accept': 'image/jpeg,image/png,image/webp'}
            ),
        }


ImagenFormSet = inlineformset_factory(
    Registro,
    Imagen,
    form=ImagenForm,
    extra=3,            # slots vacíos para subir fotos nuevas
    max_num=10,         # tope por avistamiento
    validate_max=True,
    can_delete=True,    # checkbox "Eliminar" en cada foto existente
)