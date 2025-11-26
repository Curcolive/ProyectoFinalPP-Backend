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
from django.db import IntegrityError 
from django.db import transaction 
from django.db.models import Count, Q 

from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated, AllowAny, IsAdminUser
from rest_framework import generics 
from datetime import timedelta
import traceback

from .models import Cuota, EstadoCuota, CuponPago, EstadoCupon, PasarelaPago, CuponPagoCuota, Perfil
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
)

User = get_user_model()
token_generator = PasswordResetTokenGenerator()

class PasswordResetRequestView(APIView):
  """
  POST /api/password-reset/request/
  body: { "email": "user@example.com" }
  """

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
  """
  POST /api/password-reset/confirm/
  body: { "uid": "...", "token": "...", "new_password": "nueva_clave" }
  """

  def post(self, request):
      serializer = PasswordResetConfirmSerializer(data=request.data)
      serializer.is_valid(raise_exception=True)
      serializer.save()
      return Response(
          {"message": "Contraseña actualizada correctamente."},
          status=status.HTTP_200_OK,
      )

class SignupView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = SignupSerializer
    permission_classes = [AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        return Response(
            {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "message": "Usuario creado correctamente",
            },
            status=status.HTTP_201_CREATED,
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

        try:
            # 1. Lógica de Idempotencia (sin cambios)
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
            pasarela_obj = get_object_or_404(PasarelaPago, id=pasarela_id)

            # 2. Lógica de Cupón Activo Existente (sin cambios)
            cupon_existente_activo = CuponPago.objects.filter(
                estado_cupon=estado_activo,
                cuotas_incluidas__in=cuotas_a_pagar
            ).distinct().first()

            if cupon_existente_activo:
                serializer_out = CuponPagoGeneradoSerializer(cupon_existente_activo)
                return Response(
                    {
                        "error": "Ya existe un cupón activo para una o más de estas cuotas.",
                        "cupon_existente": serializer_out.data
                    }, 
                    status=status.HTTP_409_CONFLICT
                )

            # 3. Creación del Cupón (sin cambios)
            monto_total = sum(cuota.monto for cuota in cuotas_a_pagar)
            vencimiento = timezone.now().date() + timedelta(days=7)

            nuevo_cupon = CuponPago.objects.create(
                alumno=request.user,
                estado_cupon=estado_activo,
                pasarela=pasarela_obj,
                monto_total=monto_total,
                fecha_vencimiento=vencimiento,
                idempotency_key=idempotency_key
            )
            
            # --- ¡AQUÍ ESTÁ LA CORRECCIÓN! ---
            # Cambiamos el prefijo de '/api/' a '/cupones/'
            nuevo_cupon.url_pdf = f'/cupones/cupon/{nuevo_cupon.id}/descargar/'
            # --- FIN DE LA CORRECCIÓN ---
            
            nuevo_cupon.save(update_fields=['url_pdf'])

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
             return Response({"error": "La pasarela seleccionada no existe."}, status=status.HTTP_404_NOT_FOUND)
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

class AnularCuponAlumnoAPI(APIView):
    """
    API para que un ALUMNO anule su propio cupón "Activo".
    """
    permission_classes = [IsAuthenticated] # Solo usuarios logueados

    @transaction.atomic
    def patch(self, request, pk): # 'pk' es el ID del cupón a anular
        try:
            # 1. Obtenemos todos los objetos necesarios primero
            estado_anulado = EstadoCupon.objects.get(nombre='Anulado')
            estado_activo = EstadoCupon.objects.get(nombre='Activo')
            
            # Usamos .get(pk=pk) para poder capturar el error si no existe
            cupon = CuponPago.objects.select_related('estado_cupon').get(pk=pk)

        except EstadoCupon.DoesNotExist:
            return Response({"error": "Estados 'Anulado' o 'Activo' no configurados en BD."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except CuponPago.DoesNotExist:
            return Response({"error": "El cupón no existe."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            # Captura cualquier otro error al buscar
            print(traceback.format_exc())
            return Response({"error": f"Error inesperado al buscar datos: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # --- Si encontramos todo, procedemos con la lógica ---
        try:
            # 3. VALIDACIÓN DE SEGURIDAD 1: Propietario
            if cupon.alumno != request.user:
                return Response({"error": "No tienes permiso para anular este cupón."}, status=status.HTTP_403_FORBIDDEN)

            # 4. VALIDACIÓN DE LÓGICA DE NEGOCIO: Estado
            # El alumno solo puede anular cupones que estén 'Activos'
            if cupon.estado_cupon != estado_activo:
                if cupon.estado_cupon == estado_anulado:
                    return Response({"mensaje": "Este cupón ya se encuentra anulado."}, status=status.HTTP_200_OK)
                
                # No se puede anular un cupón Pagado o Vencido
                return Response({"error": f"No se puede anular un cupón que no está 'Activo'. Estado actual: {cupon.estado_cupon.nombre}."}, status=status.HTTP_409_CONFLICT)

            # 5. Ejecutar la anulación
            cupon.estado_cupon = estado_anulado
            cupon.motivo_anulacion = "Anulado por el alumno." # Motivo automático
            cupon.save()
            
            # 6. Devolver éxito
            # 204 No Content es la respuesta estándar para un PATCH/DELETE exitoso
            return Response(status=status.HTTP_204_NO_CONTENT)

        except Exception as e:
            # Este 'except' es para errores DURANTE la lógica de anulación
            print(traceback.format_exc())
            return Response({"error": f"Error inesperado al procesar la anulación: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

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
                    cupon.cuotas_incluidas.update(estado_cuota=estado_cuota_pagada)

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