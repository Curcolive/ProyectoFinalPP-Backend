from django.db import models
from django.contrib.auth.models import User # El sistema de usuarios de Django

# --- TABLAS "MAESTRAS" O "CATÁLOGO" ---
# (Las que tu profesora dijo que el admin debe gestionar)

class EstadoCuota(models.Model):
    """
    Define si una cuota está "Pendiente", "Pagada", "Vencida", "Anulada".
    [cite_start]Corresponde a la tabla A003 del PDF [cite: 457-466] y a los círculos de 
    color en tu diseño de Figma.
    """
    nombre = models.CharField(max_length=50, unique=True)
    descripcion = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.nombre

class EstadoCupon(models.Model):
    """
    Define si un cupón está "Activo", "Expirado", "Anulado", "Pagado".
    [cite_start]Corresponde a la tabla A009 del PDF [cite: 577-587] y al estado
    en la vista del admin.
    """
    nombre = models.CharField(max_length=50, unique=True)
    descripcion = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.nombre

class PasarelaPago(models.Model):
    """
    Define las pasarelas (ej. Pago Fácil, Macro Click).
    [cite_start]Corresponde a la tabla A010 del PDF [cite: 593-606] y es lo que
    tu profesora sugirió añadir como un selector.
    """
    nombre = models.CharField(max_length=100, unique=True)
    descripcion = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.nombre

# --- TABLAS TRANSACCIONALES ---
# (El corazón de tu módulo)

class Cuota(models.Model):
    """
    Representa una deuda individual del alumno.
    [cite_start]Corresponde a la tabla A004 del PDF [cite: 474-490].
    Es CADA FILA en tu lista de "Cuotas Pendientes".
    
    [cite_start]NOTA: Tu DER [cite: 437] tiene una tabla "Alumnos" (A002) que se 
    relaciona con "Usuarios" (A001). Para simplificar, enlazamos 
    directamente al modelo 'User' de Django.
    """
    alumno = models.ForeignKey(User, on_delete=models.CASCADE, related_name="cuotas")
    estado_cuota = models.ForeignKey(EstadoCuota, on_delete=models.PROTECT, related_name="cuotas")
    
    periodo = models.CharField(max_length=100) # ej: "Cuota 6/10 - Período 2025"
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    fecha_vencimiento = models.DateField()
    
    def __str__(self):
        return f"Cuota de {self.alumno.username} - {self.periodo}"

class CuponPago(models.Model):
    """
    La "cabecera" del cupón generado.
    [cite_start]Corresponde a la tabla A008 del PDF [cite: 550-566].
    Es el resultado que se muestra en la pantalla de "Éxito".
    """
    alumno = models.ForeignKey(User, on_delete=models.CASCADE, related_name="cupones")
    estado_cupon = models.ForeignKey(EstadoCupon, on_delete=models.PROTECT, related_name="cupones")
    pasarela = models.ForeignKey(PasarelaPago, on_delete=models.PROTECT, related_name="cupones")

    monto_total = models.DecimalField(max_digits=10, decimal_places=2)
    fecha_generacion = models.DateTimeField(auto_now_add=True)
    fecha_vencimiento = models.DateField()
    
    # [cite_start]ID que nos da la pasarela (Pago Fácil, etc.) [cite: 555]
    id_externo = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    
    # URL para "Descargar Cupón (PDF)"
    url_pdf = models.URLField(max_length=500, blank=True, null=True)
    idempotency_key = models.UUIDField(unique=True, null=True, blank=True, editable=False)
    
    # Campo para el feedback del admin
    motivo_anulacion = models.TextField(blank=True, null=True)

    # --- La relación Muchos-a-Muchos ---
    # Esto le dice a Django que un Cupón se relaciona con muchas Cuotas
    # a través del modelo 'CuponPagoCuota' que definimos abajo.
    cuotas_incluidas = models.ManyToManyField(
        Cuota,
        through='CuponPagoCuota',
        related_name="cupones"
    )
    
    def __str__(self):
        return f"Cupón {self.id} de {self.alumno.username} por ${self.monto_total}"

class CuponPagoCuota(models.Model):
    """
    Esta es la tabla intermedia "detalle" que resuelve la relación M-a-M.
    [cite_start]Corresponde a la tabla A007 del PDF [cite: 529-541].
    Guarda la "foto" de cuánto valía la cuota al momento de generar el cupón.
    """
    cupon_pago = models.ForeignKey(CuponPago, on_delete=models.CASCADE)
    cuota = models.ForeignKey(Cuota, on_delete=models.PROTECT)
    
    # Guardamos el monto de la cuota en ese momento
    monto_cuota = models.DecimalField(max_digits=10, decimal_places=2) 

    class Meta:
        # Evita que se pueda añadir la misma cuota al mismo cupón dos veces
        unique_together = ('cupon_pago', 'cuota')

    def __str__(self):
        return f"Detalle: Cupón {self.cupon_pago.id} -> Cuota {self.cuota.id}"

class Perfil(models.Model):
    """
    Extiende el modelo User de Django para añadir campos específicos
    [cite_start]como DNI, Legajo y Carrera, similar a la tabla 'Alumnos' del DER [cite: 466-468].
    """
    # Enlace uno-a-uno con el modelo User de Django
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='perfil')

    # Campos adicionales requeridos
    dni = models.CharField(max_length=20, unique=True, null=True, blank=True, verbose_name="DNI")
    legajo = models.CharField(max_length=20, unique=True, null=True, blank=True, verbose_name="Legajo")
    # Usamos CharField simple para carrera por ahora
    carrera = models.CharField(max_length=100, null=True, blank=True, verbose_name="Carrera")
    # Podríamos añadir 'telefono' del DER si fuera necesario
    # telefono = models.CharField(max_length=50, null=True, blank=True)

    def __str__(self):
        # Muestra el username en el admin de Django
        return f"Perfil de {self.user.username}"

    class Meta:
        verbose_name = "Perfil de Usuario"
        verbose_name_plural = "Perfiles de Usuarios"

# --- SEÑALES PARA CREAR/ACTUALIZAR PERFIL AUTOMÁTICAMENTE ---
# Importaciones necesarias para las señales (signals)
from django.db.models.signals import post_save
from django.dispatch import receiver

@receiver(post_save, sender=User) # Esta función se ejecutará DESPUÉS de que se guarde un User
def create_or_update_user_profile(sender, instance, created, **kwargs):
    """
    Crea un Perfil automáticamente cuando se crea un nuevo User,
    o simplemente guarda el perfil existente cuando se actualiza un User.
    """
    if created:
        Perfil.objects.create(user=instance)
    # Asegura que el perfil se guarde cada vez que el usuario se guarde
    # Esto puede ser útil si el perfil tuviera lógica que dependiera del usuario
    try:
        instance.perfil.save()
    except Perfil.DoesNotExist:
        # Si por alguna razón el perfil no se creó (ej. usuario creado antes de implementar esto), lo crea ahora.
        Perfil.objects.create(user=instance)