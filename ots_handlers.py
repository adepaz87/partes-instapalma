"""
Handlers para gesión de OTs en el webhook
Se importan en app.py
"""

import os
import json
import re
from datetime import datetime
import psycopg2
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

def get_db():
    return psycopg2.connect(os.environ.get('DATABASE_URL', ''))

# ═══════════════════════════════════════════════════════════════════════════════
# FUNCIONES BASE DE OTs
# ═══════════════════════════════════════════════════════════════════════════════

def obtener_ot(numero_ot):
    """Obtiene los detalles de una OT."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, numero_ot, centro, averia, prioridad, fecha_recibida, 
                   fecha_limite, observaciones, estado, operario_asignado, 
                   fecha_resolucion, respuesta_operario, fotos_urls
            FROM ots WHERE numero_ot=%s
        """, (numero_ot,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            return {
                'id': row[0], 'numero_ot': row[1], 'centro': row[2], 'averia': row[3],
                'prioridad': row[4], 'fecha_recibida': row[5], 'fecha_limite': row[6],
                'observaciones': row[7], 'estado': row[8], 'operario_asignado': row[9],
                'fecha_resolucion': row[10], 'respuesta_operario': row[11], 'fotos_urls': row[12]
            }
        return None
    except Exception as e:
        print(f"Error obtener OT: {e}")
        return None

def listar_ots_pendientes():
    """Lista todas las OTs pendientes y asignadas."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT numero_ot, centro, averia, prioridad, estado, operario_asignado 
            FROM ots WHERE estado IN ('pendiente', 'asignada') ORDER BY created_at DESC
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows
    except Exception as e:
        print(f"Error listar OTs: {e}")
        return []

def asignar_ot(numero_ot, operario):
    """Asigna una OT a un operario."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            UPDATE ots 
            SET operario_asignado=%s, fecha_asignacion=NOW(), estado='asignada', updated_at=NOW()
            WHERE numero_ot=%s
            RETURNING id
        """, (operario, numero_ot))
        result = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return result is not None
    except Exception as e:
        print(f"Error asignar OT: {e}")
        return False

def resolver_ot(numero_ot, operario, respuesta, fotos_urls=''):
    """Marca una OT como resuelta."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            UPDATE ots 
            SET estado='resuelta', fecha_resolucion=NOW(), 
                operario_asignado=%s, respuesta_operario=%s, fotos_urls=%s, updated_at=NOW()
            WHERE numero_ot=%s
            RETURNING id
        """, (operario, respuesta, fotos_urls, numero_ot))
        result = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return result is not None
    except Exception as e:
        print(f"Error resolver OT: {e}")
        return False

def crear_ot_desde_email(numero_ot, centro, averia, email_origen, observaciones=''):
    """Crea una OT desde un email recibido."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO ots (numero_ot, centro, averia, observaciones, email_origen, origen, estado, fecha_recibida)
            VALUES (%s, %s, %s, %s, %s, 'email', 'pendiente', %s)
            ON CONFLICT (numero_ot) DO UPDATE 
            SET centro=%s, averia=%s, observaciones=%s, email_origen=%s, updated_at=NOW()
            RETURNING id
        """, (numero_ot, centro, averia, observaciones, email_origen, datetime.now().strftime('%d/%m/%Y'),
              centro, averia, observaciones, email_origen))
        ot_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return ot_id
    except Exception as e:
        print(f"Error crear OT desde email: {e}")
        return None

def crear_ot_manual(numero_ot, centro, averia, prioridad, fecha_recibida, fecha_limite, observaciones=''):
    """Crea una OT desde el formulario web."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO ots (numero_ot, centro, averia, prioridad, fecha_recibida, fecha_limite, observaciones, origen, estado)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'formulario', 'pendiente')
            ON CONFLICT (numero_ot) DO UPDATE 
            SET centro=%s, averia=%s, prioridad=%s, fecha_recibida=%s, fecha_limite=%s, observaciones=%s, updated_at=NOW()
            RETURNING id
        """, (numero_ot, centro, averia, prioridad, fecha_recibida, fecha_limite, observaciones,
              centro, averia, prioridad, fecha_recibida, fecha_limite, observaciones))
        ot_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return ot_id
    except Exception as e:
        print(f"Error crear OT manual: {e}")
        return None

# ═══════════════════════════════════════════════════════════════════════════════
# HANDLERS PARA WEBHOOK
# ═══════════════════════════════════════════════════════════════════════════════

def handle_ot_consulta(numero_ot, numero, resp, msg, use_meta, enviar_whatsapp, OPERARIOS):
    """Maneja: OT N (consultar detalle)"""
    ot = obtener_ot(numero_ot)
    if not ot:
        msg.body(f"❌ OT {numero_ot} no encontrada.")
        return
    
    centro = ot['centro'] or 'Sin especificar'
    averia = ot['averia'] or 'Sin descripción'
    prioridad = ot['prioridad'] or 'normal'
    estado = ot['estado']
    operario = ot['operario_asignado'] or 'Sin asignar'
    
    detalle = f"""🔧 *OT {numero_ot}*
━━━━━━━━━━━━━━━━━━
📍 Centro: {centro}
⚙️ Avería: {averia}
⚡ Prioridad: {prioridad.upper()}
📊 Estado: {estado.upper()}
👤 Asignado a: {operario}"""
    
    msg.body(detalle)

def handle_ot_asignar(numero_ot, nombre_operario, numero, resp, msg, use_meta, enviar_whatsapp, OPERARIOS, SUPERVISOR_WA):
    """Maneja: OT N Toño / asignar OT N a Toño"""
    # Buscar operario en el directorio por nombre
    operario_encontrado = None
    for num_tel, nombre in OPERARIOS.items():
        if normalizar(nombre) == normalizar(nombre_operario):
            operario_encontrado = nombre
            break
    
    if not operario_encontrado:
        msg.body(f"❌ Operario '{nombre_operario}' no encontrado. Revisa el nombre.")
        return
    
    if not asignar_ot(numero_ot, operario_encontrado):
        msg.body(f"❌ Error asignando OT {numero_ot}.")
        return
    
    msg.body(f"✅ OT {numero_ot} asignada a *{operario_encontrado}*")
    
    # Notificar al operario asignado
    try:
        for num_tel, nombre in OPERARIOS.items():
            if nombre == operario_encontrado:
                wa_numero = f"whatsapp:+{num_tel}" if not num_tel.startswith('whatsapp:') else num_tel
                enviar_whatsapp(wa_numero, f"📌 Te ha sido asignada la OT {numero_ot}:\n\n{obtener_ot(numero_ot)['averia']}")
                break
    except Exception as e:
        print(f"Error notificando operario: {e}")

def handle_ot_listar(numero, resp, msg, use_meta, enviar_whatsapp):
    """Maneja: listar OT / OT lista"""
    ots = listar_ots_pendientes()
    if not ots:
        msg.body("No hay OTs pendientes o asignadas en este momento.")
        return
    
    texto = "📋 *OTs Activas:*\n━━━━━━━━━━━━━━━━━\n"
    for numero_ot, centro, averia, prioridad, estado, operario in ots:
        operario_txt = operario or 'Sin asignar'
        texto += f"\n*OT {numero_ot}*\n"
        texto += f"  Centro: {centro}\n"
        texto += f"  Avería: {averia[:50]}...\n" if len(str(averia or '')) > 50 else f"  Avería: {averia}\n"
        texto += f"  Estado: {estado}\n"
        texto += f"  Asignado a: {operario_txt}\n"
    
    msg.body(texto)

def handle_ot_resolver(numero_ot, observaciones, fotos_url, numero, resp, msg, use_meta, 
                       enviar_whatsapp, OPERARIOS, SUPERVISOR_WA, GMAIL_USER, GMAIL_PASS):
    """Maneja: OT N resuelta [observaciones]"""
    # Obtener número limpio del operario
    numero_limpio = numero.replace('whatsapp:', '').replace('+', '').strip()
    nombre_operario_actual = OPERARIOS.get(numero_limpio, 'Operario Desconocido')
    
    ot = obtener_ot(numero_ot)
    if not ot:
        msg.body(f"❌ OT {numero_ot} no encontrada.")
        return
    
    # Resolver OT
    if not resolver_ot(numero_ot, nombre_operario_actual, observaciones, fotos_url):
        msg.body(f"❌ Error resolviendo OT {numero_ot}.")
        return
    
    msg.body(f"✅ OT {numero_ot} marcada como resuelta. Notificaciones enviadas.")
    
    # Notificar a Alberto por WhatsApp
    resumen_wa = f"""✅ *OT {numero_ot} — RESUELTA*
━━━━━━━━━━━━━━━━━━
👤 Operario: {nombre_operario_actual}
📍 Centro: {ot['centro']}
⚙️ Avería: {ot['averia']}
📝 Respuesta: {observaciones[:100]}...""" if len(observaciones) > 100 else f"""✅ *OT {numero_ot} — RESUELTA*
━━━━━━━━━━━━━━━━━━
👤 Operario: {nombre_operario_actual}
📍 Centro: {ot['centro']}
⚙️ Avería: {ot['averia']}
📝 Respuesta: {observaciones}"""
    
    try:
        enviar_whatsapp(SUPERVISOR_WA, resumen_wa)
    except Exception as e:
        print(f"Error notificando Alberto: {e}")
    
    # Enviar email a rbarrera y fconcepcion
    try:
        enviar_email_ot_resuelta(numero_ot, ot, nombre_operario_actual, observaciones, GMAIL_USER, GMAIL_PASS)
    except Exception as e:
        print(f"Error enviando email: {e}")
        # Notificar a Alberto por WhatsApp si falla email
        try:
            enviar_whatsapp(SUPERVISOR_WA, f"⚠️ OT {numero_ot} resuelta pero falló el envío de email a rbarrera y fconcepcion. Revisa la conexión.")
        except:
            pass

def handle_ot_crear_manual(datos, numero, resp, msg, use_meta):
    """Crea una OT desde formulario web — se llama desde /ots/new"""
    ot_id = crear_ot_manual(
        datos.get('numero_ot'),
        datos.get('centro'),
        datos.get('averia'),
        datos.get('prioridad', 'normal'),
        datos.get('fecha_recibida'),
        datos.get('fecha_limite'),
        datos.get('observaciones', '')
    )
    return ot_id

# ═══════════════════════════════════════════════════════════════════════════════
# ENVÍO DE EMAILS
# ═══════════════════════════════════════════════════════════════════════════════

def enviar_email_ot_resuelta(numero_ot, ot, operario, observaciones, gmail_user, gmail_pass):
    """Envía email a rbarrera@tomasbarretosa.com y fconcepcion@tomasbarretosa.com"""
    destinatarios = ['rbarrera@tomasbarretosa.com', 'fconcepcion@tomasbarretosa.com']
    centro = ot['centro'] or 'Sin especificar'
    averia = ot['averia'] or 'Sin descripción'
    
    msg_email = MIMEMultipart()
    msg_email['From'] = gmail_user
    msg_email['To'] = ', '.join(destinatarios)
    msg_email['Subject'] = f"OT {numero_ot} resuelta — {centro}"
    
    body_html = f"""
    <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6;">
            <div style="max-width: 600px; margin: 0 auto; border: 1px solid #ddd; border-radius: 8px; padding: 20px;">
                <h2 style="color: #27ae60;">✅ OT {numero_ot} RESUELTA</h2>
                <hr style="border: none; border-top: 2px solid #27ae60;">
                
                <p><strong>Operario:</strong> {operario}</p>
                <p><strong>Centro:</strong> {centro}</p>
                <p><strong>Avería:</strong> {averia}</p>
                <p><strong>Fecha Resolución:</strong> {datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
                
                <h3>Observaciones:</h3>
                <p style="background-color: #f5f5f5; padding: 10px; border-radius: 5px;">{observaciones}</p>
                
                <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
                <p style="font-size: 12px; color: #666;">
                    <strong>INSTAPALMA OBRAS Y SERVICIOS SLU</strong><br>
                    Sistema Automático de Gestión de OTs
                </p>
            </div>
        </body>
    </html>
    """
    
    msg_email.attach(MIMEText(body_html, 'html'))
    
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as srv:
        srv.login(gmail_user, gmail_pass)
        srv.sendmail(gmail_user, destinatarios, msg_email.as_string())

# ═══════════════════════════════════════════════════════════════════════════════
# UTILIDADES
# ═══════════════════════════════════════════════════════════════════════════════

def normalizar(texto):
    """Normaliza texto para comparación."""
    return str(texto or '').strip().lower()

