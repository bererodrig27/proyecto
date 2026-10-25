# MisionaBio/urls.py
from django.contrib.auth import views as auth_views
from django.urls import include, path

from . import views

# Cada CRUD tiene su propio namespace: base.html e index.html usan
# {% url 'especies:lista' %} y {% url 'avistamientos:crear' %}.
especies_patterns = [
    path('', views.especie_lista, name='lista'),
    path('nueva/', views.especie_crear, name='crear'),
    path('<int:pk>/', views.especie_detalle, name='detalle'),
    path('<int:pk>/editar/', views.especie_editar, name='editar'),
    path('<int:pk>/eliminar/', views.especie_eliminar, name='eliminar'),
]

avistamientos_patterns = [
    path('', views.avistamiento_lista, name='lista'),
    path('nuevo/', views.avistamiento_crear, name='crear'),
    path('<int:pk>/', views.avistamiento_detalle, name='detalle'),
    path('<int:pk>/editar/', views.avistamiento_editar, name='editar'),
    path('<int:pk>/eliminar/', views.avistamiento_eliminar, name='eliminar'),
]

urlpatterns = [
    path('', views.index, name='index'),
    path('login/', auth_views.LoginView.as_view(template_name='MisionaBio/login.html', redirect_authenticated_user=True), name='login'),
    path('registro/', views.registro, name='registro'),
    path('gestion/nuevo-admin/', views.registro_admin, name='registro_admin'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('dashboard/', views.dashboard, name='dashboard'),

    path('especies/', include((especies_patterns, 'especies'), namespace='especies')),
    path('avistamientos/', include((avistamientos_patterns, 'avistamientos'), namespace='avistamientos')),
    path('registro/', views.registro, name='registro'),
]