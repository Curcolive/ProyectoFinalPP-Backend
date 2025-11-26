from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ListaCuotasPendientesAPI,
    GenerarCuponAPI,
    HistorialCuponesAPI,
    AnularCuponAlumnoAPI,
    AdminGestionCuponesAPI,
    AnularCuponAdminAPI,
    EstadoCuponViewSet,
    PasarelaPagoViewSet,
    PasarelasDisponiblesAPI,
    AdminUpdateCuponEstadoAPI,
    DescargarCuponPDF
)

router = DefaultRouter()
router.register(r'admin/config/estados-cupon', EstadoCuponViewSet, basename='api-admin-estados-cupon')
router.register(r'admin/config/pasarelas', PasarelaPagoViewSet, basename='api-admin-pasarelas')
urlpatterns = [
    # --- Rutas de Alumno ---
    path('lista-pendientes/', ListaCuotasPendientesAPI.as_view(), name='api_lista_cuotas'),
    path('generar-cupon/', GenerarCuponAPI.as_view(), name='api_generar_cupon'),
    path('historial/', HistorialCuponesAPI.as_view(), name='api_historial_cupones'),
    path('cupon/<int:pk>/anular/', AnularCuponAlumnoAPI.as_view(), name='api_alumno_anular_cupon'),
    path('pasarelas/', PasarelasDisponiblesAPI.as_view(), name='api_pasarelas_disponibles'),

    path('cupon/<int:pk>/descargar/', DescargarCuponPDF.as_view(), name='api_descargar_cupon'),

    # --- Rutas de Administrador (manuales) ---
    path('admin/gestion/', AdminGestionCuponesAPI.as_view(), name='api_admin_gestion_cupones'),
    path('admin/anular/<int:pk>/', AnularCuponAdminAPI.as_view(), name='api_admin_anular_cupon'),
    path('admin/cupon/<int:pk>/estado/', AdminUpdateCuponEstadoAPI.as_view(), name='api_admin_update_estado',
    ),
]
urlpatterns += router.urls