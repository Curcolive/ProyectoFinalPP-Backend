from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import (
    EstadoCuota, 
    EstadoCupon, 
    PasarelaPago, 
    Cuota, 
    CuponPago,
    Perfil,
)

# --- Configuración Inline para Perfil ---
class PerfilInline(admin.StackedInline):
    """ Define cómo se mostrará el Perfil dentro de la página del User. """
    model = Perfil
    can_delete = False # No permitir borrar el perfil desde el User
    verbose_name_plural = 'Perfil de Usuario'
    fk_name = 'user'
    # Define qué campos del Perfil mostrar y en qué orden
    fields = ('dni', 'legajo', 'carrera') # Añade 'telefono' si lo creaste

# --- Extiende el Admin de User para incluir el Perfil ---
class CustomUserAdmin(BaseUserAdmin):
    """ Añade el PerfilInline a la vista de edición del User. """
    inlines = (PerfilInline,)

    # Opcional: Si quieres mostrar campos del Perfil en la LISTA de usuarios
    # list_display = ('username', 'email', 'first_name', 'last_name', 'is_staff', 'get_dni')
    # list_select_related = ('perfil',) # Optimiza la consulta

    # def get_dni(self, instance):
    #    return instance.perfil.dni
    # get_dni.short_description = 'DNI' # Nombre de la columna

    # Redefine get_inline_instances si necesitas sobreescribir lógica
    def get_inline_instances(self, request, obj=None):
        if not obj:
            return list()
        return super(CustomUserAdmin, self).get_inline_instances(request, obj)

# --- Re-registra el modelo User usando el admin personalizado ---
admin.site.unregister(User) # Des-registra el admin por defecto de User
admin.site.register(User, CustomUserAdmin) # Registra User con tu admin extendido

# Registramos los modelos "Catálogo" para que el admin pueda
# crear y editar los estados y pasarelas.
admin.site.register(EstadoCuota)
admin.site.register(EstadoCupon)
admin.site.register(PasarelaPago)

# Registramos los modelos principales (luego los 
# haremos más bonitos, por ahora solo los registramos)
admin.site.register(Cuota)
admin.site.register(CuponPago)
