# MisionaBio/views.py
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .forms import RegistroUsuarioForm


def index(request):
    return render(request, 'MisionaBio/index.html')


@login_required
def dashboard(request):
    return render(request, 'MisionaBio/dashboard.html')


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