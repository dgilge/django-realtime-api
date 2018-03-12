from django.urls import include, path

from . import views


urlpatterns = [
    path('ws/', views.ws_view, name='ws'),
    path('auth/', include('rest_framework.urls')),
]
