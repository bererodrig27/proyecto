from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import Usuario, Rol


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