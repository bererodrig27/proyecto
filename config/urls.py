# config/urls.py
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('MisionaBio.urls')),
]

# Sirve los archivos subidos (imágenes) en desarrollo. Solo funciona con DEBUG = True.
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)