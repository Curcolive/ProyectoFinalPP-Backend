from rest_framework import serializers
from django.contrib.auth.models import User
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.http import urlsafe_base64_decode
from .models import Cuota, EstadoCuota, CuponPago, EstadoCupon, PasarelaPago, Perfil, SystemLog, PagoParcial

from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

class SystemLogSerializer(serializers.ModelSerializer):
    user = serializers.StringRelatedField()

    class Meta:
        model = SystemLog
        fields = ['id', 'timestamp', 'user', 'action', 'detail']
        
User = get_user_model()
token_generator = PasswordResetTokenGenerator()


class PasswordResetRequestSerializer(serializers.Serializer):
  email = serializers.EmailField()

  def validate_email(self, value):
      return value


class PasswordResetConfirmSerializer(serializers.Serializer):
  uid = serializers.CharField()
  token = serializers.CharField()
  new_password = serializers.CharField(min_length=8, write_only=True)

  def validate(self, attrs):
      uid = attrs.get("uid")
      token = attrs.get("token")
      new_password = attrs.get("new_password")

      try:
          user_id = urlsafe_base64_decode(uid).decode()
          user = User.objects.get(pk=user_id)
      except (TypeError, ValueError, OverflowError, User.DoesNotExist):
          raise serializers.ValidationError("Enlace inválido o expirado.")

      if not token_generator.check_token(user, token):
          raise serializers.ValidationError("Enlace inválido o expirado.")

      attrs["user"] = user
      return attrs

  def save(self, **kwargs):
      user = self.validated_data["user"]
      new_password = self.validated_data["new_password"]
      user.set_password(new_password)
      user.save()
      return user

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
        model = EstadoCupon 
        fields = ['id', 'nombre', 'descripcion'] 

class PasarelaPagoSerializer(serializers.ModelSerializer):
    """
    Serializer completo para el CRUD de PasarelaPago.
    Maneja 'id', 'nombre' y 'descripcion'.
    """
    class Meta:
        model = PasarelaPago
        fields = ['id', 'nombre', 'descripcion']

class PasarelaPagoSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = PasarelaPago
        fields = ['id', 'nombre']

class AlumnoSimpleSerializer(serializers.ModelSerializer):
    """ Serializer para mostrar info básica del alumno, incluyendo DNI/Legajo/Carrera. """
    dni = serializers.CharField(source='perfil.dni', read_only=True, allow_null=True)
    legajo = serializers.CharField(source='perfil.legajo', read_only=True, allow_null=True)
    carrera = serializers.CharField(source='perfil.carrera', read_only=True, allow_null=True)
    nombre_completo = serializers.SerializerMethodField()

    class Meta:
        model = User 
        fields = ['id', 'username', 'nombre_completo', 'dni', 'legajo', 'carrera']

    def get_nombre_completo(self, obj):
        full_name = obj.get_full_name()
        return full_name if full_name else obj.username

class CuotaSerializer(serializers.ModelSerializer):
    """ Serializer para la lista de cuotas pendientes del alumno """
    estado_cuota = EstadoCuotaSerializer(read_only=True)

    class Meta:
        model = Cuota
        fields = ['id', 'periodo', 'monto', 'saldo_pendiente' 'fecha_vencimiento', 'estado_cuota']

class GenerarCuponSerializer(serializers.Serializer):
    """ Valida los datos de entrada para generar un cupón (IDs + Key) """
    cuotas_ids = serializers.ListField(
        child=serializers.IntegerField(),
        min_length=1
    )
    idempotency_key = serializers.UUIDField()
    pasarela_id = serializers.IntegerField()
    monto_parcial = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)

class PagoParcialSerializer(serializers.Serializer):
   monto = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0.01)

class CuponPagoGeneradoSerializer(serializers.ModelSerializer):
    """ Serializer para la respuesta de éxito al generar cupón """
    pasarela = PasarelaPagoSimpleSerializer(read_only=True)
    class Meta:
        model = CuponPago
        fields = ['id', 'monto_total', 'fecha_vencimiento', 'url_pdf', 'pasarela'] 

class CuponPagoListSerializer(serializers.ModelSerializer):
    """
    Serializer para mostrar la lista de cupones generados.
    Ahora incluye detalles del alumno usando AlumnoSimpleSerializer.
    """
    estado_cupon = EstadoCuponSimpleSerializer(read_only=True)
    pasarela = PasarelaPagoSimpleSerializer(read_only=True)
    alumno = AlumnoSimpleSerializer(read_only=True) 

    class Meta:
        model = CuponPago
        fields = [
            'id',
            'alumno',
            'fecha_generacion',
            'fecha_vencimiento',
            'monto_total',
            'pasarela',
            'estado_cupon',
            'url_pdf',
             'es_pago_parcial'
        ]

class MyTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['username'] = user.username
        token['is_staff'] = user.is_staff
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
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
        user.set_password(password)
        user.save()
        return user