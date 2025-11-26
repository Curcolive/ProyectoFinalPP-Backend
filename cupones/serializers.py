from rest_framework import serializers
from django.contrib.auth.models import User
from .models import Cuota, EstadoCuota, CuponPago, EstadoCupon, PasarelaPago, Perfil 
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

class EstadoCuotaSerializer(serializers.ModelSerializer):
    """ Traduce EstadoCuota a JSON (solo nombre) """
    class Meta:
        model = EstadoCuota
        fields = ['nombre']

class EstadoCuponSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = EstadoCupon
        fields = ['id', 'nombre']

class EstadoCuponSerializer(serializers.ModelSerializer):
    """
    Serializer completo para el CRUD de EstadoCupon.
    Maneja 'id', 'nombre' y 'descripcion'.
    """
    class Meta:
        model = EstadoCupon # Usa el modelo EstadoCupon
        fields = ['id', 'nombre', 'descripcion'] # Define los campos

# --- AÑADE ESTA CLASE ---
class PasarelaPagoSerializer(serializers.ModelSerializer):
    """
    Serializer completo para el CRUD de PasarelaPago.
    Maneja 'id', 'nombre' y 'descripcion'.
    """
    class Meta:
        model = PasarelaPago # <-- CAMBIO
        fields = ['id', 'nombre', 'descripcion'] # Mismos campos
# --- FIN DE LA CLASE A AÑADIR ---

class PasarelaPagoSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = PasarelaPago
        fields = ['id', 'nombre'] # <-- ¡AÑADE 'id'!

# --- SERIALIZER PARA DATOS BÁSICOS DEL ALUMNO (EL QUE FALTABA) ---
class AlumnoSimpleSerializer(serializers.ModelSerializer):
    """ Serializer para mostrar info básica del alumno, incluyendo DNI/Legajo/Carrera. """
    # Lee los campos directamente desde el modelo Perfil relacionado
    dni = serializers.CharField(source='perfil.dni', read_only=True, allow_null=True)
    legajo = serializers.CharField(source='perfil.legajo', read_only=True, allow_null=True)
    carrera = serializers.CharField(source='perfil.carrera', read_only=True, allow_null=True)
    nombre_completo = serializers.SerializerMethodField() # Campo calculado

    class Meta:
        model = User # El modelo base es User
        # Campos a incluir en el JSON
        fields = ['id', 'username', 'nombre_completo', 'dni', 'legajo', 'carrera']

    def get_nombre_completo(self, obj):
        # Función para obtener 'nombre_completo'
        full_name = obj.get_full_name() # Intenta obtener "Nombre Apellido"
        return full_name if full_name else obj.username # Si no hay nombre/apellido, usa username
# --- FIN SERIALIZER ALUMNO ---


# --- Serializers Principales ---

class CuotaSerializer(serializers.ModelSerializer):
    """ Serializer para la lista de cuotas pendientes del alumno """
    estado_cuota = EstadoCuotaSerializer(read_only=True) # Anida el nombre del estado

    class Meta:
        model = Cuota
        fields = ['id', 'periodo', 'monto', 'fecha_vencimiento', 'estado_cuota']

class GenerarCuponSerializer(serializers.Serializer):
    """ Valida los datos de entrada para generar un cupón (IDs + Key) """
    cuotas_ids = serializers.ListField(
        child=serializers.IntegerField(),
        min_length=1
    )
    idempotency_key = serializers.UUIDField()
    pasarela_id = serializers.IntegerField()

class CuponPagoGeneradoSerializer(serializers.ModelSerializer):
    """ Serializer para la respuesta de éxito al generar cupón """
    pasarela = PasarelaPagoSimpleSerializer(read_only=True)
    class Meta:
        model = CuponPago
        fields = ['id', 'monto_total', 'fecha_vencimiento', 'url_pdf', 'pasarela'] # Incluye url_pdf


# --- SERIALIZER MODIFICADO PARA LISTAS (Historial Alumno y Gestión Admin) ---
class CuponPagoListSerializer(serializers.ModelSerializer):
    """
    Serializer para mostrar la lista de cupones generados.
    Ahora incluye detalles del alumno usando AlumnoSimpleSerializer.
    """
    estado_cupon = EstadoCuponSimpleSerializer(read_only=True) # Muestra nombre del estado
    pasarela = PasarelaPagoSimpleSerializer(read_only=True) # Muestra nombre de pasarela
    # --- CAMBIO: Usa AlumnoSimpleSerializer ---
    alumno = AlumnoSimpleSerializer(read_only=True) # Muestra objeto alumno con DNI, etc.
    # -------------------------------------------

    class Meta:
        model = CuponPago
        # Incluye el campo 'alumno' en la lista de fields
        fields = [
            'id',
            'alumno', # <-- CAMBIO: Ahora devolverá { id, username, nombre_completo, dni, ... }
            'fecha_generacion',
            'fecha_vencimiento',
            'monto_total',
            'pasarela',
            'estado_cupon',
            'url_pdf'
        ]
# --- FIN SERIALIZER MODIFICADO ---


class MyTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        # Añade claims personalizados al payload del token
        token['username'] = user.username
        token['is_staff'] = user.is_staff
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        # Añade datos extra a la RESPUESTA del login
        data['username'] = self.user.username
        data['is_staff'] = self.user.is_staff
        return data

class SignupSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ["id", "username", "email", "password"]

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)  # guarda la pass encriptada
        user.save()
        return user