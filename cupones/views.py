from django.http import HttpResponse # <-- Solo necesitas HttpResponse
from django.shortcuts import redirect, get_object_or_404
# --- IMPORTA TU NUEVO GENERADOR ---
from .pdf_generator import generate_pago_facil_pdf

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated, AllowAny, IsAdminUser
from django.contrib.auth.models import User
from django.db import IntegrityError 
from django.db.models import Count, Q 
from rest_framework import generics 
from .serializers import PasarelaPagoSimpleSerializer 
import traceback
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.core.mail import send_mail
from django.conf import settings
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

# Importaciones de tus modelos y serializers
from .models import Cuota, EstadoCuota, CuponPago, EstadoCupon, PasarelaPago, CuponPagoCuota, Perfil, PagoParcial
from .serializers import (
    CuotaSerializer,
    GenerarCuponSerializer,
    CuponPagoGeneradoSerializer,
    CuponPagoListSerializer,
    MyTokenObtainPairSerializer, 
    EstadoCuponSerializer,       
    PasarelaPagoSerializer,
    EstadoCuponSimpleSerializer,
    PagoParcialSerializer
)

# Otras importaciones de Python/Django
from django.utils import timezone
from datetime import timedelta
from django.db import transaction 
# --- VISTA PERSONALIZADA PARA OBTENER TOKEN ---
from rest_framework_simplejwt.views import TokenObtainPairView


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

            # 3. Creación del Cupón - Ahora con soporte para pago parcial
            # Usar saldo_pendiente si existe, sino usar monto original
            saldo_total_cuotas = sum(
                cuota.saldo_pendiente if cuota.saldo_pendiente is not None else cuota.monto 
                for cuota in cuotas_a_pagar
            )
            
            # Determinar el monto final del cupón
            es_parcial = False
            if monto_parcial and monto_parcial > 0:
                # Si se especificó un monto parcial, usarlo
                monto_final = monto_parcial
                # Es parcial si el monto es menor que el saldo pendiente
                if monto_parcial < saldo_total_cuotas:
                    es_parcial = True
            else:
                # Usar el saldo pendiente total
                monto_final = saldo_total_cuotas
            
            vencimiento = timezone.now().date() + timedelta(days=7)

            nuevo_cupon = CuponPago.objects.create(
                alumno=request.user,
                estado_cupon=estado_activo,
                pasarela=pasarela_obj,
                monto_total=monto_final,
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
            
            # Si el cupón se marca como "Pagado", actualizar las cuotas
            if nuevo_estado_cupon.nombre == 'Pagado':
                try:
                    estado_cuota_pagada = EstadoCuota.objects.get(nombre='Pagada')
                    
                    # Procesar cada cuota incluida en el cupón
                    for cuota in cupon.cuotas_incluidas.all():
                        # Inicializar saldo_pendiente si es None
                        if cuota.saldo_pendiente is None:
                            cuota.saldo_pendiente = cuota.monto
                        
                        if cupon.es_pago_parcial:
                            # Es pago parcial: descontar monto del cupón del saldo
                            cuota.saldo_pendiente = cuota.saldo_pendiente - cupon.monto_total
                            
                            # Si el saldo llega a 0 o menos, marcar como Pagada
                            if cuota.saldo_pendiente <= 0:
                                cuota.saldo_pendiente = 0
                                cuota.estado_cuota = estado_cuota_pagada
                            # Si aún queda saldo, no cambiar el estado (sigue Pendiente)
                        else:
                            # Pago completo: saldo = 0 y estado = Pagada
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
            # 1. Buscar la cuota
            cuota = get_object_or_404(Cuota.objects.select_related('estado_cuota', 'alumno'), pk=pk)
            
            # 2. Validar permisos (solo el dueño o admin)
            if cuota.alumno != request.user and not request.user.is_staff:
                return Response({"error": "No tienes permiso para pagar esta cuota."}, status=status.HTTP_403_FORBIDDEN)
            
            # 3. Validar datos de entrada
            serializer = PagoParcialSerializer(data=request.data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
            monto_pago = serializer.validated_data['monto']
            
            # 4. Inicializar saldo si es None
            if cuota.saldo_pendiente is None:
                cuota.saldo_pendiente = cuota.monto
            
            # 5. Validar monto
            if monto_pago > cuota.saldo_pendiente:
                return Response(
                    {"error": f"El monto ({monto_pago}) excede el saldo pendiente ({cuota.saldo_pendiente})."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # 6. Registrar el pago
            pago = PagoParcial.objects.create(
                cuota=cuota,
                monto=monto_pago,
                medio_pago="Macro Click"
            )
            
            # 7. Actualizar saldo
            cuota.saldo_pendiente = cuota.saldo_pendiente - monto_pago
            
            # 8. Si saldo = 0, cambiar estado a Pagada
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


# --- VISTAS DE AUTENTICACIÓN ---

class SignupView(APIView):
    """
    Vista para registrar nuevos usuarios.
    POST: Crea un nuevo usuario con username, first_name, last_name, email y password.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        username = request.data.get('username')
        first_name = request.data.get('first_name', '')
        last_name = request.data.get('last_name', '')
        email = request.data.get('email', '')
        password = request.data.get('password')

        if not username or not password:
            return Response(
                {"detail": "El nombre de usuario y la contraseña son obligatorios."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if User.objects.filter(username=username).exists():
            return Response(
                {"detail": "El nombre de usuario ya está en uso."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if email and User.objects.filter(email=email).exists():
            return Response(
                {"detail": "El email ya está registrado."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            user = User.objects.create_user(
                username=username,
                password=password,
                email=email,
                first_name=first_name,
                last_name=last_name
            )
            return Response({
                "detail": "Usuario creado exitosamente.",
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name
                }
            }, status=status.HTTP_201_CREATED)
        except Exception as e:
            print(traceback.format_exc())
            return Response(
                {"detail": f"Error al crear usuario: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class PasswordResetRequestView(APIView):
    """
    Vista para solicitar un enlace de recuperación de contraseña.
    POST: Envía un email con un enlace para restablecer la contraseña.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get('email')

        if not email:
            return Response(
                {"detail": "El email es obligatorio."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            # Por seguridad, no revelamos si el email existe o no
            return Response({
                "message": "Si el email está registrado, recibirás un enlace de recuperación."
            }, status=status.HTTP_200_OK)

        # Generar token y uid
        token = default_token_generator.make_token(user)
        uid = urlsafe_base64_encode(force_bytes(user.pk))

        # Construir URL de recuperación
        frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')
        reset_url = f"{frontend_url}/reset-password?uid={uid}&token={token}"

        # Enviar email (usando el backend configurado en settings.py)
        try:
            send_mail(
                subject="Recuperación de contraseña - Sistema de Cuotas",
                message=f"Hola {user.first_name or user.username},\n\n"
                        f"Recibimos una solicitud para restablecer tu contraseña.\n\n"
                        f"Hacé clic en el siguiente enlace para continuar:\n{reset_url}\n\n"
                        f"Si no solicitaste esto, ignora este mensaje.\n\n"
                        f"Saludos,\nEquipo de Soporte",
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@example.com'),
                recipient_list=[email],
                fail_silently=False,
            )
        except Exception as e:
            print(f"Error al enviar email: {e}")
            # No fallamos silenciosamente para que el usuario sepa que hubo un problema
            pass

        return Response({
            "message": "Si el email está registrado, recibirás un enlace de recuperación."
        }, status=status.HTTP_200_OK)


class PasswordResetConfirmView(APIView):
    """
    Vista para confirmar el cambio de contraseña.
    POST: Cambia la contraseña del usuario si el token es válido.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        uid = request.data.get('uid')
        token = request.data.get('token')
        new_password = request.data.get('new_password')

        if not uid or not token or not new_password:
            return Response(
                {"detail": "Todos los campos son obligatorios."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            user_id = force_str(urlsafe_base64_decode(uid))
            user = User.objects.get(pk=user_id)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            return Response(
                {"detail": "Enlace de recuperación inválido."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not default_token_generator.check_token(user, token):
            return Response(
                {"detail": "El enlace de recuperación ha expirado o es inválido."},
                status=status.HTTP_400_BAD_REQUEST
            )

        user.set_password(new_password)
        user.save()

        return Response({
            "message": "Contraseña actualizada correctamente. Ya podés iniciar sesión."
        }, status=status.HTTP_200_OK)


class GoogleLoginView(APIView):
    """
    Vista para autenticación con Google OAuth.
    POST: Valida el token de Google y crea/autentica al usuario.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        credential = request.data.get('credential')

        if not credential:
            return Response(
                {"detail": "El token de Google es obligatorio."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Verificar el token de Google
            google_client_id = getattr(settings, 'GOOGLE_CLIENT_ID', None)
            if not google_client_id:
                return Response(
                    {"detail": "Google OAuth no está configurado."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

            idinfo = id_token.verify_oauth2_token(
                credential,
                google_requests.Request(),
                google_client_id
            )

            email = idinfo.get('email')
            first_name = idinfo.get('given_name', '')
            last_name = idinfo.get('family_name', '')

            if not email:
                return Response(
                    {"detail": "No se pudo obtener el email de Google."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Buscar o crear usuario
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    'username': email.split('@')[0],
                    'first_name': first_name,
                    'last_name': last_name
                }
            )

            if created:
                # Usuario nuevo: necesita completar perfil (crear contraseña)
                user.set_unusable_password()
                user.save()
                return Response({
                    "require_password": True,
                    "user": {
                        "id": user.id,
                        "email": user.email,
                        "first_name": user.first_name,
                        "last_name": user.last_name
                    }
                }, status=status.HTTP_200_OK)
            else:
                # Usuario existente: generar tokens JWT
                from rest_framework_simplejwt.tokens import RefreshToken
                refresh = RefreshToken.for_user(user)
                
                # Añadir claims personalizados
                refresh['username'] = user.username
                refresh['is_staff'] = user.is_staff

                return Response({
                    "access": str(refresh.access_token),
                    "refresh": str(refresh),
                    "username": user.username,
                    "is_staff": user.is_staff,
                    "require_password": False
                }, status=status.HTTP_200_OK)

        except ValueError as e:
            print(f"Error verificando token de Google: {e}")
            return Response(
                {"detail": "Token de Google inválido."},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            print(traceback.format_exc())
            return Response(
                {"detail": f"Error al procesar login con Google: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class CompleteProfileView(APIView):
    """
    Vista para completar el perfil de un usuario nuevo (después de login con Google).
    POST: Actualiza username, nombre, apellido y establece la contraseña.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        user_id = request.data.get('user_id')
        username = request.data.get('username')
        first_name = request.data.get('first_name', '')
        last_name = request.data.get('last_name', '')
        password = request.data.get('password')

        if not user_id or not username or not password:
            return Response(
                {"detail": "user_id, username y password son obligatorios."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return Response(
                {"detail": "Usuario no encontrado."},
                status=status.HTTP_404_NOT_FOUND
            )

        # Verificar que el username no esté en uso por otro usuario
        if User.objects.filter(username=username).exclude(pk=user_id).exists():
            return Response(
                {"detail": "El nombre de usuario ya está en uso."},
                status=status.HTTP_400_BAD_REQUEST
            )

        user.username = username
        user.first_name = first_name
        user.last_name = last_name
        user.set_password(password)
        user.save()

        return Response({
            "detail": "Perfil completado exitosamente.",
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name
            }
        }, status=status.HTTP_200_OK)
