from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from datetime import date

# Importamos tus modelos exactos de models.py
from ...models import CuponPago, EstadoCupon 

class Command(BaseCommand):
    help = 'Busca y actualiza el estado de los cupones "Activos" a "Expirado" si su fecha de vencimiento ya pasó.'

    def handle(self, *args, **options):

        # Obtenemos la fecha de hoy
        today = date.today()

        self.stdout.write(self.style.NOTICE(f'Iniciando job de expiración de cupones para la fecha: {today}'))

        try:
            # 1. Obtenemos los estados que necesitamos (basado en tu models.py)
            estado_activo = EstadoCupon.objects.get(nombre="Activo")
            estado_expirado = EstadoCupon.objects.get(nombre="Expirado")

        except EstadoCupon.DoesNotExist as e:
            raise CommandError(f'Error: No se encontró uno de los estados requeridos ("Activo" o "Expirado"). Asegúrate de que existan en la base de datos. Detalle: {e}')

        # 2. Buscamos los cupones que cumplen la condición:
        # - Están "Activos"
        # - Su fecha de vencimiento es MENOR QUE (<) hoy
        cupones_a_expirar = CuponPago.objects.filter(
            estado_cupon=estado_activo,
            fecha_vencimiento__lt=today
        )

        # 3. Actualizamos todos los cupones encontrados en una sola consulta
        # El método .update() devuelve el número de filas afectadas
        count = cupones_a_expirar.update(estado_cupon=estado_expirado)

        if count > 0:
            self.stdout.write(self.style.SUCCESS(f'¡Éxito! Se actualizaron {count} cupones a "Expirado".'))
        else:
            self.stdout.write(self.style.SUCCESS('No se encontraron cupones para expirar.'))