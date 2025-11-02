from django.contrib import admin
from django.urls import path, include
# Importa la vista de refresco por defecto Y TU VISTA PERSONALIZADA
from rest_framework_simplejwt.views import TokenRefreshView
from cupones.views import MyTokenObtainPairView # <-- ¡IMPORTA TU VISTA DESDE CUPONES!

urlpatterns = [
    path('admin/', admin.site.urls),

    # Conecta las URLs de la app 'cupones'
    path('cupones/', include('cupones.urls')),

    # --- URLs de Autenticación JWT ---
    # Usa tu vista personalizada para obtener el token (login)
    path('api/token/', MyTokenObtainPairView.as_view(), name='token_obtain_pair'),
    # Usa la vista por defecto para refrescar el token
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    # --- FIN URLs JWT ---
]