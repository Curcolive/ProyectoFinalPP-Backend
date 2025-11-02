from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated, AllowAny, IsAdminUser
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404
from django.db import IntegrityError # <-- Para atrapar errores de borrado
from django.db.models import Count, Case, When, Value, Q # <-- Para estadísticas
import traceback

# Importaciones de tus modelos y serializers
from .models import Cuota, EstadoCuota, CuponPago, EstadoCupon, PasarelaPago, CuponPagoCuota, Perfil
from .serializers import (
    CuotaSerializer,
    GenerarCuponSerializer,
    CuponPagoGeneradoSerializer,
    CuponPagoListSerializer,
    MyTokenObtainPairSerializer, # Para login
    EstadoCuponSerializer,      # Para CRUD de EstadoCupon
    EstadoCuponSimpleSerializer
)

# Otras importaciones de Python/Django
from django.utils import timezone
from datetime import timedelta
from django.db import transaction # Para asegurar la integridad de la BD


class ListaCuotasPendientesAPI(APIView):
    """ API para obtener la lista de cuotas pendientes del alumno. """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            estados_pendientes = EstadoCuota.objects.filter(
                nombre__in=['Pendiente', 'Vencida']
            )
            if not estados_pendientes.exists():
                 return Response({"error": "Estados 'Pendiente' o 'Vencida' no encontrados."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            return Response({"error": f"Error al buscar estados: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        try:
            cuotas = Cuota.objects.filter(
                alumno=request.user,
                estado_cuota__in=estados_pendientes
            ).order_by('fecha_vencimiento')
        except Exception as e:
             return Response({"error": f"Error al buscar cuotas: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        try:
            serializer = CuotaSerializer(cuotas, many=True)
        except Exception as e:
            return Response({"error": f"Error al serializar cuotas: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(serializer.data, status=status.HTTP_200_OK)


class GenerarCuponAPI(APIView):
    """ API para generar un nuevo cupón de pago. """
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        serializer_in = GenerarCuponSerializer(data=request.data)
        if not serializer_in.is_valid():
            return Response(serializer_in.errors, status=status.HTTP_400_BAD_REQUEST)

        cuotas_ids = serializer_in.validated_data['cuotas_ids']
        idempotency_key = serializer_in.validated_data['idempotency_key']

        try:
            cupon_existente = CuponPago.objects.filter(idempotency_key=idempotency_key).first()
            if cupon_existente:
                serializer_out = CuponPagoGeneradoSerializer(cupon_existente)
                return Response(serializer_out.data, status=status.HTTP_200_OK)

            cuotas_a_pagar = Cuota.objects.filter(
                id__in=cuotas_ids,
                alumno=request.user
            )

            if len(cuotas_a_pagar) != len(cuotas_ids):
                return Response({"error": "Una o más cuotas no se encontraron o no pertenecen a este usuario."}, status=status.HTTP_404_NOT_FOUND)

            estado_activo = EstadoCupon.objects.get(nombre="Activo")
            cuotas_con_cupon_activo = cuotas_a_pagar.filter(cupones__estado_cupon=estado_activo)

            if cuotas_con_cupon_activo.exists():
                return Response({"error": "Una o más de las cuotas seleccionadas ya tienen un cupón activo generado."}, status=status.HTTP_409_CONFLICT)

            monto_total = sum(cuota.monto for cuota in cuotas_a_pagar)
            pasarela_pf = PasarelaPago.objects.get(nombre="Pago Fácil")
            vencimiento = timezone.now().date() + timedelta(days=7)

            nuevo_cupon = CuponPago.objects.create(
                alumno=request.user,
                estado_cupon=estado_activo,
                pasarela=pasarela_pf,
                monto_total=monto_total,
                fecha_vencimiento=vencimiento,
                idempotency_key=idempotency_key,
                url_pdf='http://localhost:3000/cupon_ejemplo.pdf' # URL simulada
            )

            for cuota in cuotas_a_pagar:
                CuponPagoCuota.objects.create(
                    cupon_pago=nuevo_cupon,
                    cuota=cuota,
                    monto_cuota=cuota.monto
                )

            serializer_out = CuponPagoGeneradoSerializer(nuevo_cupon)
            return Response(serializer_out.data, status=status.HTTP_201_CREATED)

        except EstadoCupon.DoesNotExist:
            return Response({"error": "Estado 'Activo' no configurado en BD."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except PasarelaPago.DoesNotExist:
            return Response({"error": "Pasarela 'Pago Fácil' no configurada en BD."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            print(traceback.format_exc())
            return Response({"error": f"Error inesperado en el servidor: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class HistorialCuponesAPI(APIView):
    """ API para el historial de cupones del alumno. """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            cupones = CuponPago.objects.filter(
                alumno=request.user
            ).order_by('-fecha_generacion')
            serializer = CuponPagoListSerializer(cupones, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            print(traceback.format_exc())
            return Response({"error": f"Error inesperado al buscar historial: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# --- VISTA PERSONALIZADA PARA OBTENER TOKEN ---
from rest_framework_simplejwt.views import TokenObtainPairView

class MyTokenObtainPairView(TokenObtainPairView):
    """ Usa el serializer personalizado para añadir 'username' y 'is_staff' """
    serializer_class = MyTokenObtainPairSerializer


# --- VISTAS DE ADMINISTRADOR ---

class AdminGestionCuponesAPI(APIView):
    """ API para la gestión de cobranzas (Admin) """
    permission_classes = [IsAdminUser]

    def get(self, request):
            try:
                # Cálculo de estadísticas (como antes)
                estadisticas = CuponPago.objects.aggregate(
                    total=Count('id'),
                    activos=Count('id', filter=Q(estado_cupon__nombre='Activo')),
                    pagados=Count('id', filter=Q(estado_cupon__nombre='Pagado')),
                    vencidos=Count('id', filter=Q(estado_cupon__nombre='Vencido')),
                    anulados=Count('id', filter=Q(estado_cupon__nombre='Anulado'))
                )
                
                # Búsqueda de cupones (como antes)
                cupones = CuponPago.objects.select_related(
                    'alumno__perfil', 'pasarela', 'estado_cupon'
                ).order_by('-fecha_generacion')
                
                # --- NUEVO: Obtener todas las opciones de estado ---
                opciones_estado_objs = EstadoCupon.objects.all()
                opciones_estado_serializer = EstadoCuponSimpleSerializer(opciones_estado_objs, many=True)
                # --- FIN NUEVO ---

                # Prepara la respuesta de la lista
                cupones_serializer = CuponPagoListSerializer(cupones, many=True)
                
                # Combina todo en la respuesta
                respuesta_data = {
                    'estadisticas': estadisticas,
                    'cupones': cupones_serializer.data,
                    'opciones_estado': opciones_estado_serializer.data # <-- AÑADIDO
                }
                
                return Response(respuesta_data, status=status.HTTP_200_OK)

            except Exception as e:
                print(traceback.format_exc())
                return Response({"error": f"Error inesperado al buscar cupones: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class AnularCuponAdminAPI(APIView):
    """ API para anular un cupón (Admin) """
    permission_classes = [IsAdminUser]

    @transaction.atomic
    def patch(self, request, pk):
        motivo = request.data.get('motivo', '').strip()
        if not motivo:
            return Response({"error": "El motivo de anulación es obligatorio."}, status=status.HTTP_400_BAD_REQUEST)
        
        cupon = get_object_or_404(CuponPago.objects.select_related('estado_cupon'), pk=pk)
        
        if cupon.estado_cupon.nombre == 'Pagado':
            return Response({"error": "No se puede anular un cupón que ya está pagado."}, status=status.HTTP_409_CONFLICT)
        if cupon.estado_cupon.nombre == 'Anulado':
            return Response({"mensaje": "Este cupón ya se encuentra anulado."}, status=status.HTTP_200_OK)
        
        try:
            estado_anulado = EstadoCupon.objects.get(nombre='Anulado')
            cupon.estado_cupon = estado_anulado
            cupon.motivo_anulacion = motivo
            cupon.save()
            serializer = CuponPagoListSerializer(cupon)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except EstadoCupon.DoesNotExist:
            return Response({"error": "El estado 'Anulado' no está configurado en la base de datos."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            print(traceback.format_exc())
            return Response({"error": f"Error inesperado al anular el cupón: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# --- VIEWSET PARA CRUD DE ESTADO CUPÓN (CON MANEJO DE ERROR DE BORRADO) ---
class EstadoCuponViewSet(viewsets.ModelViewSet):
    """
    ViewSet para CRUD de EstadoCupon.
    Maneja IntegrityError al eliminar.
    """
    queryset = EstadoCupon.objects.all().order_by('id')
    serializer_class = EstadoCuponSerializer
    permission_classes = [IsAdminUser]

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object() # Obtiene el objeto a eliminar
        try:
            # Intenta eliminar
            instance.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except IntegrityError:
            # ¡Atrapa el error si está en uso!
            return Response(
                {"detail": "No se puede eliminar este estado porque está siendo utilizado por uno o más cupones de pago."},
                status=status.HTTP_409_CONFLICT # 409 Conflicto
            )
        except Exception as e:
            # Otros errores
            return Response(
                {"detail": f"Error inesperado al intentar eliminar: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
# --- FIN VIEWSET ---

class AdminUpdateCuponEstadoAPI(APIView):
    """
    API para que un admin actualice el estado de un CuponPago específico.
    Recibe: {"estado_cupon_id": N}
    """
    permission_classes = [IsAdminUser]

    def patch(self, request, pk): # pk es el ID del CUPÓN
        try:
            # Busca el nuevo ID de estado del cuerpo de la petición
            nuevo_estado_id = request.data.get('estado_cupon_id')
            if not nuevo_estado_id:
                return Response({"error": "Falta 'estado_cupon_id'."}, status=status.HTTP_400_BAD_REQUEST)

            # Busca el estado y el cupón
            nuevo_estado = get_object_or_404(EstadoCupon, id=nuevo_estado_id)
            cupon = get_object_or_404(CuponPago, pk=pk)
            
            # Actualiza y guarda
            cupon.estado_cupon = nuevo_estado
            cupon.save()
            
            # Devuelve el cupón actualizado
            serializer = CuponPagoListSerializer(cupon)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except (EstadoCupon.DoesNotExist, CuponPago.DoesNotExist):
            return Response({"error": "El cupón o el estado no existen."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            print(traceback.format_exc())
            return Response({"error": f"Error inesperado: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
# --- FIN NUEVA VISTA ---