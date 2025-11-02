from django.urls import path, include
from rest_framework.routers import DefaultRouter # <-- Importa el Router
from .views import (
    ListaCuotasPendientesAPI,
    GenerarCuponAPI,
    HistorialCuponesAPI,
    AdminGestionCuponesAPI,
    AnularCuponAdminAPI,
    EstadoCuponViewSet, # <-- Importa el nuevo ViewSet
    AdminUpdateCuponEstadoAPI
)

# --- CONFIGURACIÓN DEL ROUTER ---
# 1. Crea un router
router = DefaultRouter()
# 2. Registra tu ViewSet.
# 'admin/config/estados-cupon' será la URL base
router.register(r'admin/config/estados-cupon', EstadoCuponViewSet, basename='api-admin-estados-cupon')
# (Aquí registraremos luego EstadoCuotaViewSet, PasarelaPagoViewSet)
# -------------------------------

# Define la lista de URLs (las APIViews manuales)
urlpatterns = [
    # --- Rutas de Alumno ---
    path('lista-pendientes/', ListaCuotasPendientesAPI.as_view(), name='api_lista_cuotas'),
    path('generar-cupon/', GenerarCuponAPI.as_view(), name='api_generar_cupon'),
    path('historial/', HistorialCuponesAPI.as_view(), name='api_historial_cupones'),

    # --- Rutas de Administrador (manuales) ---
    path('admin/gestion/', AdminGestionCuponesAPI.as_view(), name='api_admin_gestion_cupones'),
    path('admin/anular/<int:pk>/', AnularCuponAdminAPI.as_view(), name='api_admin_anular_cupon'),
    path('admin/cupon/<int:pk>/estado/', AdminUpdateCuponEstadoAPI.as_view(), name='api_admin_update_estado'
    ),
]

# --- AÑADE LAS RUTAS DEL ROUTER ---
# 3. Añade las URLs generadas por el router a tu lista
urlpatterns += router.urls
# ---------------------------------