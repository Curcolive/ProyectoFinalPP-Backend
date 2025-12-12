from django.http import HttpResponse
from django.shortcuts import redirect, get_object_or_404
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password
from django.db import IntegrityError 
from django.db import transaction 
from django.db.models import Count, Q 
from django_filters.rest_framework import DjangoFilterBackend

from rest_framework.generics import ListAPIView
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets, generics
from rest_framework.permissions import IsAuthenticated, AllowAny, IsAdminUser
from rest_framework.filters import OrderingFilter
from rest_framework.pagination import PageNumberPagination

from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

from datetime import timedelta
import traceback

from .logging_utils import create_log, log_action

from .models import Cuota, EstadoCuota, CuponPago, EstadoCupon, PasarelaPago, CuponPagoCuota, Perfil, SystemLog
from .pdf_generator import generate_pago_facil_pdf
from .serializers import (
    CuotaSerializer,
    GenerarCuponSerializer,
    CuponPagoGeneradoSerializer,
    CuponPagoListSerializer,
    MyTokenObtainPairSerializer, 
    EstadoCuponSerializer,       
    PasarelaPagoSerializer,
    PasarelaPagoSimpleSerializer,
    EstadoCuponSimpleSerializer,
    SignupSerializer,
    PasswordResetRequestSerializer,
    PasswordResetConfirmSerializer,
    SystemLogSerializer,
    PagoParcialSerializer,
)

class LogsPagination(PageNumberPagination):
    page_size = 30
    page_size_query_param = 'page_size'
    max_page_size = 200


class SystemLogListAPI(ListAPIView):
    permission_classes = [IsAdminUser] 
    serializer_class = SystemLogSerializer
    queryset = SystemLog.objects.all()
    pagination_class = LogsPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['user', 'action']
    ordering_fields = ['timestamp']
    ordering = ['-timestamp']

User = get_user_model()
token_generator = PasswordResetTokenGenerator()

class CompleteProfileView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        user_id = request.data.get("user_id")
        username = request.data.get("username")
        first_name = request.data.get("first_name")
        last_name = request.data.get("last_name")
        password = request.data.get("password")

        if not all([user_id, username, first_name, last_name, password]):
            return Response({"detail": "Todos los campos son obligatorios."}, status=400)

        User = get_user_model()
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({"detail": "Usuario no encontrado."}, status=404)

        user.username = username
        user.first_name = first_name
        user.last_name = last_name
        user.set_password(password)
        user.save()

        return Response({"detail": "Perfil completado exitosamente."})

class GoogleLoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        credential = request.data.get("credential")
        if not credential:
            log_action(None, SystemLog.ACTION_LOGIN_FAIL, "Google login sin token")
            return Response({"detail": "Falta el token de Google."}, status=400)

        try:
            idinfo = id_token.verify_oauth2_token(
                credential,
                google_requests.Request(),
                settings.GOOGLE_CLIENT_ID,
            )
        except Exception:
            log_action(None, SystemLog.ACTION_LOGIN_FAIL, "Token inválido de Google")
            return Response({"detail": "Token de Google inválido."}, status=400)

        email = idinfo.get("email")
        given_name = idinfo.get("given_name", "")
        family_name = idinfo.get("family_name", "")

        if not email:
            log_action(None, SystemLog.ACTION_LOGIN_FAIL, "Login Google sin email")
            return Response({"detail": "No se obtuvo email desde Google."}, status=400)

        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                "username": email,
                "first_name": given_name,
                "last_name": family_name,
            },
        )

        if created:
            user.set_unusable_password()
            user.save()
            log_action(user, SystemLog.ACTION_LOGIN, "Nuevo usuario creado desde Google")
        else:
            log_action(user, SystemLog.ACTION_LOGIN, "Login Google exitoso")

        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "refresh": str(refresh),
                "access": str(refresh.access_token),
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "username": user.username,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                },
                "just_created": created,
                "require_password": not user.has_usable_password(),
            },
            status=status.HTTP_200_OK,
        )
    

class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return Response(
                {"message": "Si el correo existe, se enviará un enlace de recuperación."},
                status=status.HTTP_200_OK,
            )

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = token_generator.make_token(user)

        frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:5173")
        reset_link = f"{frontend_url}/reset-password?uid={uid}&token={token}"

        subject = "Recuperar contraseña"
        message = f"Hola {user.username},\n\nPara restablecer tu contraseña haz clic en el siguiente enlace:\n{reset_link}\n\nSi no solicitaste este cambio, ignora este correo."
        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None)

        send_mail(subject, message, from_email, [user.email])

        return Response(
            {"message": "Si el correo existe, se enviará un enlace de recuperación."},
            status=status.HTTP_200_OK,
        )


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {"message": "Contraseña actualizada correctamente."},
            status=status.HTTP_200_OK,
        )

class SignupView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        data = request.data

        required_fields = ["username", "first_name", "last_name", "email", "password"]
        for f in required_fields:
            if not data.get(f):
                return Response({"detail": f"El campo {f} es obligatorio."}, status=400)

        if User.objects.filter(username=data["username"]).exists():
            return Response({"detail": "El usuario ya existe."}, status=400)

        if User.objects.filter(email=data["email"]).exists():
            return Response({"detail": "Ese email ya está registrado."}, status=400)

        user = User.objects.create(
            username=data["username"],
            first_name=data["first_name"],
            last_name=data["last_name"],
            email=data["email"],
            password=make_password(data["password"]),
        )

        return Response(
            {"detail": "Usuario creado correctamente."},
            status=status.HTTP_201_CREATED
        )

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
        pasarela_id = serializer_in.validated_data['pasarela_id']
        monto_parcial = serializer_in.validated_data.get('monto_parcial', None)

        try:
            cupon_existente = CuponPago.objects.filter(idempotency_key=idempotency_key).first()
            if cupon_existente:
                log_action(request.user, SystemLog.ACTION_COUPON,
                        f"Reutiliza cupón existente ID={cupon_existente.id}")
                serializer_out = CuponPagoGeneradoSerializer(cupon_existente)
                return Response(serializer_out.data, status=status.HTTP_200_OK)

            cuotas_a_pagar = Cuota.objects.filter(
                id__in=cuotas_ids,
                alumno=request.user
            )

            if len(cuotas_a_pagar) != len(cuotas_ids):
                log_action(request.user, SystemLog.ACTION_COUPON_FAIL,
                        f"Cuotas inválidas: {cuotas_ids}")
                return Response({"error": "Una o más cuotas no se encontraron o no pertenecen a este usuario."},
                                status=status.HTTP_404_NOT_FOUND)

            estado_activo = EstadoCupon.objects.get(nombre="Activo")
            pasarela_obj = get_object_or_404(PasarelaPago, id=pasarela_id)

            cupon_existente_activo = CuponPago.objects.filter(
                estado_cupon=estado_activo,
                cuotas_incluidas__in=cuotas_a_pagar
            ).distinct().first()

            if cupon_existente_activo:
                log_action(request.user, SystemLog.ACTION_COUPON_FAIL,
                        f"Ya existía cupón activo ID={cupon_existente_activo.id}")
                serializer_out = CuponPagoGeneradoSerializer(cupon_existente_activo)
                return Response(
                    {
                        "error": "Ya existe un cupón activo para una o más de estas cuotas.",
                        "cupon_existente": serializer_out.data
                    },
                    status=status.HTTP_409_CONFLICT
                )
            
            saldo_total_cuotas = sum(
                cuota.saldo_pendiente if cuota.saldo_pendiente is not None else cuota.monto 
                for cuota in cuotas_a_pagar
            )
            es_parcial = False
            if monto_parcial and monto_parcial > 0:
                monto_final = monto_parcial
                if monto_parcial < saldo_total_cuotas:
                    es_parcial = True
                monto_final = saldo_total_cuotas

            monto_total = sum(cuota.monto for cuota in cuotas_a_pagar)
            vencimiento = timezone.now().date() + timedelta(days=7)

            nuevo_cupon = CuponPago.objects.create(
                alumno=request.user,
                estado_cupon=estado_activo,
                pasarela=pasarela_obj,
                monto_total=monto_total,
                fecha_vencimiento=vencimiento,
                idempotency_key=idempotency_key,
                es_pago_parcial=es_parcial
            )

            nuevo_cupon.url_pdf = f'/cupones/cupon/{nuevo_cupon.id}/descargar/'
            nuevo_cupon.save(update_fields=['url_pdf'])

            for cuota in cuotas_a_pagar:
                CuponPagoCuota.objects.create(
                    cupon_pago=nuevo_cupon,
                    cuota=cuota,
                    monto_cuota=cuota.monto
                )

            log_action(request.user, SystemLog.ACTION_COUPON,
                    f"Generado cupón ID={nuevo_cupon.id}, total={monto_total}")

            serializer_out = CuponPagoGeneradoSerializer(nuevo_cupon)
            return Response(serializer_out.data, status=status.HTTP_201_CREATED)
             
        except Exception as e:
            log_action(request.user, SystemLog.ACTION_COUPON_FAIL, str(e))
            print(traceback.format_exc())
            return Response({"error": f"Error inesperado en el servidor: {str(e)}"},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class AnularCuponAlumnoAPI(APIView):
    """
    API para que un ALUMNO anule su propio cupón "Activo".
    """
    permission_classes = [IsAuthenticated]
    @transaction.atomic
    def patch(self, request, pk):
        try:
            estado_anulado = EstadoCupon.objects.get(nombre='Anulado')
            estado_activo = EstadoCupon.objects.get(nombre='Activo')
            cupon = CuponPago.objects.select_related('estado_cupon').get(pk=pk)

        except EstadoCupon.DoesNotExist:
            log_action(request.user, SystemLog.ACTION_COUPON_FAIL,
                       f"Estado 'Anulado' no existe en BD al anular ID={pk}")
            return Response({"error": "Estados 'Anulado' o 'Activo' no configurados en BD."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except CuponPago.DoesNotExist:
            log_action(request.user, SystemLog.ACTION_COUPON_FAIL,
                       f"Estado 'Anulado' no existe en BD al anular ID={pk}")
            return Response({"error": "El cupón no existe."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            log_action(request.user, SystemLog.ACTION_COUPON_FAIL,
                       f"Error inesperado al anular cupón ID={pk} - {str(e)}")
            print(traceback.format_exc())
            return Response({"error": f"Error inesperado al buscar datos: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        try:
            if cupon.alumno != request.user:
                log_action(request.user, SystemLog.ACTION_COUPON_FAIL,
                       f"El alumno no tiene permiso para anular este cupón  ID={pk}")
                return Response({"error": "No tienes permiso para anular este cupón."}, status=status.HTTP_403_FORBIDDEN)

            if cupon.estado_cupon != estado_activo:
                if cupon.estado_cupon == estado_anulado:
                    log_action(request.user, SystemLog.ACTION_COUPON_FAIL,
                            f"Error , el cupón ya se encuentra anulado. ID={pk} - {str(e)}")
                    return Response({"mensaje": "Este cupón ya se encuentra anulado."}, status=status.HTTP_200_OK)

                return Response({"error": f"No se puede anular un cupón que no está 'Activo'. Estado actual: {cupon.estado_cupon.nombre}."}, status=status.HTTP_409_CONFLICT)

            cupon.estado_cupon = estado_anulado
            cupon.motivo_anulacion = "Anulado por el alumno."
            cupon.save()
            log_action(request.user, SystemLog.ACTION_COUPON_CANCEL,
                       f"Cupón ID={pk} anulado. Motivo=Anulado por el alumno.")
            
            return Response(status=status.HTTP_204_NO_CONTENT)

        except Exception as e:
            log_action(request.user, SystemLog.ACTION_COUPON_FAIL,
                       f"Error inesperado al anular cupón ID={pk} - {str(e)}")
            print(traceback.format_exc())
            return Response({"error": f"Error inesperado al procesar la anulación: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

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
            log_action(request.user, SystemLog.ACTION_COUPON_FAIL,
                       f"Intento anulación sin motivo - cupon ID={pk}")
            return Response({"error": "El motivo de anulación es obligatorio."}, status=status.HTTP_400_BAD_REQUEST)
        
        cupon = get_object_or_404(CuponPago.objects.select_related('estado_cupon'), pk=pk)
        
        if cupon.estado_cupon.nombre == 'Pagado':
            log_action(request.user, SystemLog.ACTION_COUPON_FAIL,
                       f"No se puede anular cupón pagado - ID={pk}")
            return Response({"error": "No se puede anular un cupón que ya está pagado."}, status=status.HTTP_409_CONFLICT)

        if cupon.estado_cupon.nombre == 'Anulado':
            log_action(request.user, SystemLog.ACTION_COUPON_FAIL,
                       f"Intento anular cupón ya anulado - ID={pk}")
            return Response({"mensaje": "Este cupón ya se encuentra anulado."}, status=status.HTTP_200_OK)

        try:
            estado_anulado = EstadoCupon.objects.get(nombre='Anulado')
            cupon.estado_cupon = estado_anulado
            cupon.motivo_anulacion = motivo
            cupon.save()

            log_action(request.user, SystemLog.ACTION_COUPON_CANCEL,
                       f"Cupón ID={pk} anulado. Motivo={motivo}")

            serializer = CuponPagoListSerializer(cupon)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except EstadoCupon.DoesNotExist:
            log_action(request.user, SystemLog.ACTION_COUPON_FAIL,
                       f"Estado 'Anulado' no existe en BD al anular ID={pk}")
            return Response({"error": "El estado 'Anulado' no está configurado en la base de datos."},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        except Exception as e:
            log_action(request.user, SystemLog.ACTION_COUPON_FAIL,
                       f"Error inesperado al anular cupón ID={pk} - {str(e)}")
            print(traceback.format_exc())
            return Response({"error": f"Error inesperado al anular el cupón: {str(e)}"},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


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

class PasarelaPagoViewSet(viewsets.ModelViewSet):
    """
    ViewSet para CRUD de PasarelaPago.
    Maneja IntegrityError al eliminar.
    """
    queryset = PasarelaPago.objects.all().order_by('id') # <-- CAMBIO
    serializer_class = PasarelaPagoSerializer           # <-- CAMBIO
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
                # Texto personalizado para pasarelas
                {"detail": "No se puede eliminar esta pasarela porque está siendo utilizada por uno o más cupones de pago."},
                status=status.HTTP_409_CONFLICT # 409 Conflicto
            )
        except Exception as e:
            # Otros errores
            return Response(
                {"detail": f"Error inesperado al intentar eliminar: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class AdminUpdateCuponEstadoAPI(APIView):
    """
    API para que un admin actualice el estado de un CuponPago específico.
    Recibe: {"estado_cupon_id": N}
    """
    permission_classes = [IsAdminUser]

    @transaction.atomic
    def patch(self, request, pk): # pk es el ID del CUPÓN
        try:
            nuevo_estado_id = request.data.get('estado_cupon_id')
            if not nuevo_estado_id:
                return Response({"error": "Falta 'estado_cupon_id'."}, status=status.HTTP_400_BAD_REQUEST)

            nuevo_estado_cupon = get_object_or_404(EstadoCupon, id=nuevo_estado_id)
            cupon = get_object_or_404(CuponPago.objects.prefetch_related('cuotas_incluidas'), pk=pk)
            
            cupon.estado_cupon = nuevo_estado_cupon
            cupon.save()
            
            if nuevo_estado_cupon.nombre == 'Pagado':
                try:
                 estado_cuota_pagada = EstadoCuota.objects.get(nombre='Pagada')
                 for cuota in cupon.cuotas_incluidas.all():
                        if cuota.saldo_pendiente is None:
                         cuota.saldo_pendiente = cuota.monto 
                        if cupon.es_pago_parcial:
                         cupon.cuotas_incluidas.update(estado_cuota=estado_cuota_pagada)
                         cuota.saldo_pendiente = cuota.saldo_pendiente - cupon.monto_total
                        if cuota.saldo_pendiente <= 0:
                                cuota.saldo_pendiente = 0
                                cuota.estado_cuota = estado_cuota_pagada
                        else:
                                cuota.saldo_pendiente = 0
                                cuota.estado_cuota = estado_cuota_pagada
                        
                        cuota.save()

                except EstadoCuota.DoesNotExist:
                    return Response({"error": "El estado 'Pagada' no existe en la tabla EstadoCuota. No se pudo completar la operación."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
            serializer = CuponPagoListSerializer(cupon)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except (EstadoCupon.DoesNotExist, CuponPago.DoesNotExist):
            return Response({"error": "El cupón o el estado no existen."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            print(traceback.format_exc())
            return Response({"error": f"Error inesperado: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class PasarelasDisponiblesAPI(generics.ListAPIView):
    """
    API simple de SOLO LECTURA para que el alumno
    vea las pasarelas de pago disponibles.
    """
    permission_classes = [IsAuthenticated] # Solo usuarios logueados
    queryset = PasarelaPago.objects.all().order_by('nombre')
    serializer_class = PasarelaPagoSimpleSerializer # Reutiliza el serializer simple


# --- PEGAR ESTA NUEVA CLASE AL FINAL DE TODO views.py ---
class DescargarCuponPDF(APIView):
    """
    Entrega el PDF de un cupón de pago.
    Llama a pdf_generator si es "Pago Fácil".
    Redirige si es otra pasarela.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            # Obtenemos el cupón con toda la info relacionada necesaria
            cupon = get_object_or_404(
                CuponPago.objects.select_related(
                    'alumno__perfil', 
                    'pasarela'
                ).prefetch_related('cuotas_incluidas'), # Optimizamos consulta
                pk=pk
            )
        except CuponPago.DoesNotExist:
            return HttpResponse("Cupón no encontrado.", status=404)

        # Validación de seguridad: solo el dueño o un admin pueden ver el cupón
        if cupon.alumno != request.user and not request.user.is_staff:
            return HttpResponse("No tienes permiso para acceder a este cupón.", status=403)

        # --- LÓGICA CONDICIONAL (MUCHO MÁS LIMPIA) ---
        if cupon.pasarela.nombre.lower() == 'pago fácil':
            # 1. Es Pago Fácil: Llamar al generador
            try:
                buffer = generate_pago_facil_pdf(cupon)
                
                filename = f"cupon_pago_{cupon.id}.pdf"
                # 'inline' abre el PDF en el navegador
                return HttpResponse(buffer, content_type='application/pdf', headers={'Content-Disposition': f'inline; filename="{filename}"'})
            
            except Exception as e:
                print(f"Error al generar PDF: {e}")
                return HttpResponse(f"Error al generar el PDF: {e}", status=500)

        else:
            # 2. Es otra pasarela: Redirigir a cupón genérico (simulación)
            return redirect('http://localhost:3000/cupon_ejemplo.pdf')
class RegistrarPagoParcialAPI(APIView):
    """
    API para registrar un pago parcial sobre una cuota.
    POST /cuotas/<id>/pagar/
    Recibe: { "monto": 1500.00 }
    """
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk):
        try:
            cuota = get_object_or_404(Cuota.objects.select_related('estado_cuota', 'alumno'), pk=pk)
            
            if cuota.alumno != request.user and not request.user.is_staff:
                return Response({"error": "No tienes permiso para pagar esta cuota."}, status=status.HTTP_403_FORBIDDEN)
            
            serializer = PagoParcialSerializer(data=request.data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
            monto_pago = serializer.validated_data['monto']
        
            if cuota.saldo_pendiente is None:
                cuota.saldo_pendiente = cuota.monto
            
            if monto_pago > cuota.saldo_pendiente:
                return Response(
                    {"error": f"El monto ({monto_pago}) excede el saldo pendiente ({cuota.saldo_pendiente})."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            pago = PagoParcial.objects.create(
                cuota=cuota,
                monto=monto_pago,
                medio_pago="Macro Click"
            )
            
            cuota.saldo_pendiente = cuota.saldo_pendiente - monto_pago
            
            if cuota.saldo_pendiente <= 0:
                try:
                    estado_pagada = EstadoCuota.objects.get(nombre='Pagada')
                    cuota.estado_cuota = estado_pagada
                except EstadoCuota.DoesNotExist:
                    pass  # Si no existe el estado, solo actualizamos el saldo
            
            cuota.save()
            
            # 9. Retornar cuota actualizada
            cuota_serializer = CuotaSerializer(cuota)
            return Response({
                "mensaje": "Pago registrado exitosamente.",
                "pago_id": pago.id,
                "cuota": cuota_serializer.data
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            print(traceback.format_exc())
            return Response({"error": f"Error inesperado: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
