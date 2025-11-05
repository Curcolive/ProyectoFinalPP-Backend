import io
import os
from django.conf import settings
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF

# Importas los modelos que necesitas para los datos
from .models import CuponPago 

def generate_pago_facil_pdf(cupon):
    """
    Función que usa ReportLab para crear un PDF similar
    al ejemplo cuponDePago_1093.pdf.
    Recibe un objeto 'cupon' ya consultado.
    """
    # --- 1. Configuración inicial ---
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4 # A4 es (21*cm, 29.7*cm)
    
    # --- 2. Datos ---
    alumno = cupon.alumno
    perfil = alumno.perfil
    # Usamos .all() ya que la vista hizo prefetch_related
    cuotas = cupon.cuotas_incluidas.all().order_by('fecha_vencimiento')
    
    # --- 3. Dibujar PDF (coordenadas (0,0) es abajo-izquierda) ---
    
    # --- Encabezado (Ya corregido) ---
    p.setFont("Helvetica-Bold", 16)
    p.drawString(3*cm, height - 3*cm, "INSTITUTO SUPERIOR DEL MILAGRO N° 8207")
    p.setFont("Helvetica", 10)
    p.drawString(3*cm, height - 3.5*cm, "Dirección: Alvarado 951, Salta")
    p.setFont("Helvetica-Bold", 14)
    p.drawRightString(width - 3*cm, height - 4.5*cm, f"CUPÓN DE PAGO N° {cupon.id}")
    p.setFont("Helvetica", 10)
    p.drawRightString(width - 3*cm, height - 5*cm, f"Factura impresa el día: {cupon.fecha_generacion.strftime('%d/%m/%Y %H:%M')}")
    p.line(2*cm, height - 6*cm, width - 2*cm, height - 6*cm) # Línea divisoria
    # --- FIN Encabezado ---

    # --- DATOS DEL ALUMNO (Re-ubicado) ---
    p.setFont("Helvetica-Bold", 12)
    p.drawString(3*cm, height - 7*cm, "DATOS DEL ALUMNO/A") 
    p.setFont("Helvetica", 10)
    p.drawString(3*cm, height - 7.5*cm, f"Alumno: {alumno.get_full_name() or alumno.username}")
    p.drawString(3*cm, height - 8*cm, f"Documento: {perfil.dni or 'No especificado'}")
    p.drawString(3*cm, height - 8.5*cm, f"N. de Legajo: {perfil.legajo or 'No especificado'}")
    p.drawString(3*cm, height - 9*cm, f"Carrera: {perfil.carrera or 'No especificada'}")
    
    # --- DETALLE DE CUOTAS (Re-ubicado) ---
    p.setFont("Helvetica-Bold", 12)
    p.drawString(2*cm, height - 10.5*cm, "DATOS DE LOS MESES A PAGAR")
    
    # Encabezados de tabla
    y_tabla = height - 11.5*cm
    p.setFont("Helvetica-Bold", 10)
    p.drawString(3*cm, y_tabla, "MES / PERIODO")
    p.drawRightString(width - 3*cm, y_tabla, "PRECIO DE LA CUOTA")
    p.line(2*cm, y_tabla - 0.5*cm, width - 2*cm, y_tabla - 0.5*cm)
    
    # Filas de la tabla
    y_actual = y_tabla - 1.5*cm
    p.setFont("Helvetica", 10)
    for cuota in cuotas:
        p.drawString(3*cm, y_actual, f"{cuota.periodo} (Vence: {cuota.fecha_vencimiento.strftime('%d/%m/%Y')})")
        p.drawRightString(width - 3*cm, y_actual, f"${cuota.monto:,.2f}")
        y_actual -= 0.7*cm # Siguiente fila

    # Total
    p.line(2*cm, y_actual, width - 2*cm, y_actual)
    y_actual -= 1*cm
    p.setFont("Helvetica-Bold", 14)
    p.drawRightString(width - 3*cm, y_actual, f"TOTAL: ${cupon.monto_total:,.2f}")

    
    # --- INICIO FOOTER (CON LOGO Y QR) ---
    
    # --- ¡AQUÍ ESTÁ LA CORRECCIÓN! ---
    # Bajamos la base del footer de 10cm a 5cm
    footer_y_base = 5*cm # Posición vertical base para el footer
    # --- FIN DE LA CORRECCIÓN ---

    # QR Simulado
    qr_code = qr.QrCodeWidget('https://www.pagofacil.com.ar')
    bounds = qr_code.getBounds()
    width_qr = bounds[2] - bounds[0]
    height_qr = bounds[3] - bounds[1]
    
    qr_size = 4*cm # Tamaño del QR
    d = Drawing(qr_size, qr_size, transform=[qr_size/width_qr, 0, 0, qr_size/height_qr, 0, 0])
    d.add(qr_code)
    renderPDF.draw(d, p, 3*cm, footer_y_base) # Posiciona el QR
    
    p.setFont("Helvetica-Bold", 10)
    # Posicionamos el texto relativo a la base del footer
    p.drawString(3*cm, footer_y_base - 0.5*cm, "CUPÓN DE PAGO PARA PAGAR EN LOCALES")


    # Logo Pago Fácil
    try:
        logo_path = os.path.join(settings.BASE_DIR, 'cupones', 'static', 'logo-pago-facil.png')
        if os.path.exists(logo_path):
             # Dibuja el logo a la derecha del QR, usando la misma base
             p.drawImage(logo_path, width - 9*cm, footer_y_base, width=6*cm, preserveAspectRatio=True, mask='auto')
        else:
             p.drawString(width - 9*cm, footer_y_base, "[Logo Pago Fácil no encontrado]")
    except Exception as e:
        print(f"Error al cargar logo: {e}")
        p.drawString(width - 9*cm, footer_y_base, "[Error al cargar logo]")


    # Simulación de código de barras (al fondo de la página)
    p.setFont("Helvetica", 10)
    barcode_string = f"0966007210600...{perfil.dni or '00000000'}...{int(cupon.monto_total * 100)}"
    p.drawCentredString(width/2, 3*cm, barcode_string)
    p.line(2*cm, 2.5*cm, width - 2*cm, 2.5*cm)
    
    # --- 4. Finalizar y devolver PDF ---
    p.showPage()
    p.save()
    
    buffer.seek(0)
    # Devuelve el buffer; la vista se encargará de crear el HttpResponse
    return buffer