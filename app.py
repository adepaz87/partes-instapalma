#!/usr/bin/env python3
"""
Bot de Partes de Trabajo — Instapalma
Webhook para Twilio WhatsApp
"""

from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
import json
import os
import smtplib
import base64
import io
import psycopg2
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from datetime import datetime

# PDF
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable, Image as RLImage
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER

app = Flask(__name__)

# ── Base de datos ──────────────────────────────────────────────────────────────
def get_db():
    return psycopg2.connect(os.environ.get('DATABASE_URL', ''))

def init_db():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS partes (
                id SERIAL PRIMARY KEY,
                numero_parte VARCHAR(20),
                fecha VARCHAR(20),
                operario VARCHAR(100),
                cliente TEXT,
                obra TEXT,
                operarios TEXT,
                albaranes TEXT,
                material_stock TEXT,
                descripcion TEXT,
                terminado TEXT,
                tiempo_restante TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversaciones_db (
                numero VARCHAR(50) PRIMARY KEY,
                paso VARCHAR(50),
                datos JSONB,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vehiculos (
                id SERIAL PRIMARY KEY,
                matricula VARCHAR(20),
                marca_modelo VARCHAR(100),
                mes VARCHAR(20),
                km_inicio VARCHAR(20),
                km_fin VARCHAR(20),
                proximo_aceite VARCHAR(20),
                estado_neumaticos TEXT,
                conductores TEXT,
                mantenimientos TEXT,
                observaciones TEXT,
                golpes TEXT,
                operario VARCHAR(100),
                created_at TIMESTAMP DEFAULT NOW(),
                pdf_descargado BOOLEAN DEFAULT FALSE,
                pdf_descargado_at TIMESTAMP
            )
        """)
        conn.commit()
        print("DB inicializada OK")
    except Exception as e:
        print(f"Error init DB: {e}")

def guardar_parte(datos, numero_operario):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO partes (numero_parte, fecha, operario, cliente, obra, operarios, albaranes, material_stock, descripcion, terminado, tiempo_restante)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            datos.get('numero_parte'),
            datos.get('fecha'),
            numero_operario,
            datos.get('cliente'),
            datos.get('obra'),
            datos.get('operarios'),
            datos.get('albaranes'),
            datos.get('material_stock'),
            datos.get('descripcion'),
            datos.get('terminado'),
            datos.get('tiempo_restante'),
        ))
        parte_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        print(f"Parte guardado en DB OK — id={parte_id}")
        return parte_id
    except Exception as e:
        print(f"Error guardando parte: {e}")
        return None

with app.app_context():
    init_db()

# ── Configuración ─────────────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID', '')
TWILIO_AUTH_TOKEN  = os.environ.get('TWILIO_AUTH_TOKEN', '')
TWILIO_WA_NUMBER   = os.environ.get('TWILIO_WA_NUMBER', 'whatsapp:+15554087014')
SUPERVISOR_EMAIL_1 = 'alberto@adpb.es'
SUPERVISOR_EMAIL_2 = 'adm2@adpb.es'
SUPERVISOR_WA      = os.environ.get('SUPERVISOR_WA', 'whatsapp:+34690875940')
SUPERVISOR_WA_2    = 'whatsapp:+34654893491'
GMAIL_USER         = os.environ.get('GMAIL_USER', '')
GMAIL_APP_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '')

# Directorio de operarios: número → nombre
OPERARIOS = {
    '34636606175': 'Jonathan Rodríguez',
    '34616233640': 'Toño Guardia',
    '34666123020': 'Antonio J. Pérez',
    '34689448068': 'Airam',
    '34616908968': 'Moisés',
    '34628380158': 'Petter',
    '34689069588': 'Iker',
    '34690875940': 'Alberto',
    '34606544007': 'Adolfo Castro',
}

def nombre_operario(numero):
    """Devuelve 'Nombre (6XXXXXXXX)' a partir de un número WhatsApp."""
    limpio = numero.replace('whatsapp:','').replace('+','').strip()
    nombre = OPERARIOS.get(limpio, '')
    corto  = limpio[2:] if limpio.startswith('34') else limpio
    return f"{nombre} ({corto})" if nombre else corto

# ── Estado de conversaciones (persistido en DB) ────────────────────────────────
def get_estado(numero):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT paso, datos FROM conversaciones_db WHERE numero=%s", (numero,))
        row = cur.fetchone()
        cur.close(); conn.close()
        if row:
            return {'paso': row[0], 'datos': row[1]}
        return None
    except:
        return None

def set_paso(numero, paso):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            INSERT INTO conversaciones_db (numero, paso, datos, updated_at)
            VALUES (%s, %s, '{}'::jsonb, NOW())
            ON CONFLICT (numero) DO UPDATE SET paso=%s, updated_at=NOW()
        """, (numero, paso, paso))
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        print(f"Error set_paso: {e}")

def set_dato(numero, clave, valor):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            UPDATE conversaciones_db
            SET datos = datos || jsonb_build_object(%s, %s::text), updated_at=NOW()
            WHERE numero=%s
        """, (clave, valor, numero))
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        print(f"Error set_dato: {e}")

def borrar_estado(numero):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("DELETE FROM conversaciones_db WHERE numero=%s", (numero,))
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        print(f"Error borrar_estado: {e}")

def iniciar_parte(numero):
    try:
        conn = get_db(); cur = conn.cursor()
        datos = json.dumps({
            'operario': numero,
            'cliente': '', 'obra': '', 'operarios': '',
            'albaranes': '', 'material_stock': '', 'descripcion': '',
            'terminado': '', 'tiempo_restante': '',
            'fecha': datetime.now().strftime('%d/%m/%Y')
        })
        cur.execute("""
            INSERT INTO conversaciones_db (numero, paso, datos, updated_at)
            VALUES (%s, 'cliente', %s::jsonb, NOW())
            ON CONFLICT (numero) DO UPDATE SET paso='cliente', datos=%s::jsonb, updated_at=NOW()
        """, (numero, datos, datos))
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        print(f"Error iniciar_parte: {e}")


MENSAJES_INICIO = ['parte', 'parte de trabajo', 'nuevo parte', 'abrir parte', 'crear parte', 'hola']

def normalizar(texto):
    return texto.strip().lower()
def es_confirmacion(texto):
    return normalizar(texto) in ['si', 'sí', 'ok', 'vale', 'correcto', 'confirmado', 's', 'yes']
def es_cancelacion(texto):
    return normalizar(texto) in ['no', 'cancelar', 'cancel']


def limpiar_nombre_archivo(texto):
    """Elimina caracteres especiales para nombres de archivo seguros."""
    import unicodedata
    texto = unicodedata.normalize('NFD', texto)
    texto = ''.join(c for c in texto if unicodedata.category(c) != 'Mn')
    texto = texto.replace(' ', '_').replace('/', '-').replace('\\', '-')
    texto = ''.join(c for c in texto if c.isalnum() or c in '_-.')
    return texto.upper()

def generar_pdf(datos):
    """Genera el PDF del parte y devuelve los bytes."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)

    elements = []
    AZUL = colors.HexColor('#1a3a5c')
    GRIS = colors.HexColor('#f5f5f5')

    titulo_style = ParagraphStyle('titulo', fontSize=20, textColor=AZUL,
        alignment=TA_CENTER, spaceAfter=4, fontName='Helvetica-Bold')
    sub_style = ParagraphStyle('sub', fontSize=10, textColor=colors.grey,
        alignment=TA_CENTER, spaceAfter=16)
    sec_style = ParagraphStyle('sec', fontSize=9, textColor=colors.white,
        backColor=AZUL, fontName='Helvetica-Bold', spaceAfter=0, spaceBefore=6, borderPad=4)
    pie_style = ParagraphStyle('pie', fontSize=7, textColor=colors.grey, alignment=TA_CENTER)

    # Cabecera con logo
    import os as _os
    LOGO_PATH = _os.path.join(_os.path.dirname(__file__), 'logo.jpg')
    if _os.path.exists(LOGO_PATH):
        _logo_ratio = 1024 / 219
        _logo_w = 4 * cm
        _logo_h = _logo_w / _logo_ratio
        logo_img = RLImage(LOGO_PATH, width=_logo_w, height=_logo_h)
    else:
        logo_img = Paragraph("INSTAPALMA", titulo_style)
    cab_title = ParagraphStyle('cab_title', fontName='Helvetica-Bold', fontSize=16,
        textColor=AZUL, alignment=1, leading=20)
    cab = Table([[logo_img, Paragraph('PARTE DE TRABAJO', cab_title)]], colWidths=[5*cm, 12*cm])
    cab.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
    ]))
    elements.append(cab)
    elements.append(HRFlowable(width="100%", thickness=2, color=AZUL, spaceAfter=6))
    elements.append(Spacer(1, 0.2*cm))

    # Cabecera — solo fecha
    t_cab = Table([['Fecha', datos['fecha']]],
        colWidths=[3*cm, 14*cm])
    t_cab.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,-1), GRIS),
        ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('TEXTCOLOR', (0,0), (0,-1), AZUL),
        ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
        ('PADDING', (0,0), (-1,-1), 6),
    ]))
    elements.append(t_cab)
    elements.append(Spacer(1, 0.3*cm))

    # Cliente y Obra
    t_obra = Table([
        ['Cliente', datos['cliente']],
        ['Obra', datos['obra']],
    ], colWidths=[3*cm, 14*cm])
    t_obra.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,-1), AZUL),
        ('TEXTCOLOR', (0,0), (0,-1), colors.white),
        ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 10),
        ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
        ('PADDING', (0,0), (-1,-1), 7),
        ('ROWBACKGROUNDS', (1,0), (1,-1), [colors.white, GRIS]),
    ]))
    elements.append(t_obra)
    elements.append(Spacer(1, 0.3*cm))

    # Operarios
    elements.append(Paragraph("OPERARIOS Y HORAS", sec_style))
    ops_rows = [['Operario', 'Horas']]
    for linea in datos['operarios'].split('\n'):
        linea = linea.strip()
        if not linea:
            continue
        # Separadores: —, -, :, o patrón "NOMBRE Xh" al final
        import re as _re
        m = _re.match(r'^(.+?)\s*[—\-:]\s*(\d[\d.,]*\s*h(?:oras?|rs?)?)$', linea, _re.IGNORECASE)
        if not m:
            m = _re.match(r'^(.+?)\s+(\d[\d.,]*\s*h(?:oras?|rs?)?)$', linea, _re.IGNORECASE)
        if m:
            ops_rows.append([m.group(1).strip(), m.group(2).strip()])
        else:
            ops_rows.append([linea, ''])
    t_ops = Table(ops_rows, colWidths=[13*cm, 4*cm])
    t_ops.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), AZUL),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
        ('PADDING', (0,0), (-1,-1), 6),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, GRIS]),
        ('ALIGN', (1,0), (1,-1), 'CENTER'),
    ]))
    elements.append(t_ops)
    elements.append(Spacer(1, 0.3*cm))

    # Albaranes — una sola columna
    elements.append(Paragraph("ALBARANES", sec_style))
    alb_texto = datos.get('albaranes', '')
    if normalizar(alb_texto) in ['ninguno', 'no', ''] or not alb_texto:
        alb_contenido = '—'
    else:
        alb_contenido = alb_texto.strip()
    t_alb = Table([['Albaranes'], [alb_contenido]], colWidths=[17*cm])
    t_alb.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), AZUL),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
        ('PADDING', (0,0), (-1,-1), 6),
        ('BACKGROUND', (0,1), (-1,-1), colors.white),
    ]))
    elements.append(t_alb)
    elements.append(Spacer(1, 0.3*cm))

    # Material de stock
    elements.append(Paragraph("MATERIAL DE STOCK", sec_style))
    mat = datos.get('material_stock', 'Ninguno')
    if normalizar(mat) == 'ninguno' or not mat:
        mat_rows = [['Material', 'Cantidad'], ['—', '—']]
    else:
        mat_rows = [['Material', 'Cantidad']]
        for linea in mat.split('\n'):
            linea = linea.strip()
            if not linea:
                continue
            if '—' in linea:
                parts = linea.split('—', 1)
            elif '-' in linea:
                parts = linea.split('-', 1)
            else:
                parts = [linea, '']
            mat_rows.append([parts[0].strip(), parts[1].strip() if len(parts) > 1 else ''])
    t_mat = Table(mat_rows, colWidths=[10*cm, 7*cm])
    t_mat.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), AZUL),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
        ('PADDING', (0,0), (-1,-1), 6),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, GRIS]),
    ]))
    elements.append(t_mat)
    elements.append(Spacer(1, 0.3*cm))

    # Descripción
    elements.append(Paragraph("DESCRIPCION DE TRABAJOS", sec_style))
    t_desc = Table([[datos.get('descripcion', '')]], colWidths=[17*cm])
    t_desc.setStyle(TableStyle([
        ('FONTSIZE', (0,0), (-1,-1), 10),
        ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
        ('PADDING', (0,0), (-1,-1), 8),
        ('BACKGROUND', (0,0), (-1,-1), colors.white),
        ('MINROWHEIGHT', (0,0), (-1,-1), 60),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    elements.append(t_desc)
    elements.append(Spacer(1, 0.3*cm))

    # Estado del trabajo
    terminado = datos.get('terminado', '')
    tiempo_restante = datos.get('tiempo_restante', '')
    estado_texto = 'TERMINADO ✓' if normalizar(terminado) in ['sí','si'] else f'EN CURSO — Tiempo restante: {tiempo_restante}'
    VERDE = colors.HexColor('#2e7d32')
    NARANJA = colors.HexColor('#e65100')
    color_estado = VERDE if normalizar(terminado) in ['sí','si'] else NARANJA
    t_estado = Table([[f'ESTADO: {estado_texto}']], colWidths=[17*cm])
    t_estado.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), color_estado),
        ('TEXTCOLOR', (0,0), (-1,-1), colors.white),
        ('FONTNAME', (0,0), (-1,-1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 11),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('PADDING', (0,0), (-1,-1), 8),
    ]))
    elements.append(t_estado)

    elements.append(Spacer(1, 1*cm))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey))
    elements.append(Paragraph(
        f"Generado el {datetime.now().strftime('%d/%m/%Y %H:%M')} — Instapalma",
        pie_style))

    doc.build(elements)
    buffer.seek(0)
    return buffer.read()

def enviar_email_gmail(datos, numero_operario, pdf_bytes=None):
    """Envía el parte por email con el PDF adjunto usando SMTP + Gmail App Password."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders

    user     = os.environ.get('GMAIL_USER', '')
    password = os.environ.get('GMAIL_APP_PASSWORD', '')
    if not user or not password:
        print("GMAIL no configurado")
        return False
    try:
        destinatarios = ['alberto@adpb.es', 'adm2@adpb.es']
        ops   = datos.get('operarios', 'Ninguno')
        term  = datos.get('terminado', '-')
        trem  = datos.get('tiempo_restante', '')
        estado = f"✅ Terminado" if 'í' in term.lower() or term.lower()=='si' else f"🔄 No terminado — {trem}"

        msg = MIMEMultipart()
        msg['From']    = user
        msg['To']      = ', '.join(destinatarios)
        msg['Subject'] = f"Parte {datos.get('numero_parte')} — {datos.get('cliente')} | {datos.get('obra')} | {datos.get('fecha')}"

        cuerpo = (
            f"Parte de trabajo confirmado.\n\n"
            f"Nº Parte: {datos.get('numero_parte')}\n"
            f"Fecha: {datos.get('fecha')}\n"
            f"Cliente: {datos.get('cliente')}\n"
            f"Obra: {datos.get('obra')}\n"
            f"Operarios:\n{ops}\n"
            f"Albaranes: {datos.get('albaranes','Ninguno')}\n"
            f"Material stock: {datos.get('material_stock','Ninguno')}\n"
            f"Descripción: {datos.get('descripcion','-')}\n"
            f"Estado: {estado}\n\n"
            f"Adjunto el PDF del parte."
        )
        msg.attach(MIMEText(cuerpo, 'plain', 'utf-8'))

        if pdf_bytes:
            part = MIMEBase('application', 'pdf')
            part.set_payload(pdf_bytes)
            encoders.encode_base64(part)
            fecha_pdf = datos.get('fecha', '').replace('/', '-').replace(' ', '')
            obra_pdf = limpiar_nombre_archivo(datos.get('obra', 'obra'))
            ops_raw = datos.get('operarios', '')
            ops_lista = [limpiar_nombre_archivo(l.split('—')[0].split('-')[0].strip().split()[0]) for l in ops_raw.split('\n') if l.strip()]
            ops_pdf = '-'.join(ops_lista) if ops_lista else 'OPERARIOS'
            nombre_pdf = f"{fecha_pdf}-{obra_pdf}-{ops_pdf}.pdf"
            part.add_header('Content-Disposition', f'attachment; filename="{nombre_pdf}"')
            msg.attach(part)

        with smtplib.SMTP('smtp.gmail.com', 587, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.login(user, password)
            server.sendmail(user, destinatarios, msg.as_bytes())
        print("Email enviado OK")
        return True
    except Exception as e:
        print(f"Error enviando email: {e}")
        return False

def enviar_whatsapp(destino, mensaje, media_url=None):
    try:
        import urllib.request as _ur
        # Limpiar el número destino (quitar whatsapp: y +)
        to_number = destino.replace('whatsapp:', '').replace('+', '').strip()
        META_TOKEN = os.environ.get('META_TOKEN', '')
        PHONE_ID   = os.environ.get('META_PHONE_ID', '1214142395112377')
        if media_url:
            # Enviar documento/imagen
            payload = {
                "messaging_product": "whatsapp",
                "to": to_number,
                "type": "document",
                "document": {"link": media_url, "caption": mensaje}
            }
        else:
            payload = {
                "messaging_product": "whatsapp",
                "to": to_number,
                "type": "text",
                "text": {"body": mensaje, "preview_url": False}
            }
        data = json.dumps(payload).encode()
        req = _ur.Request(
            f"https://graph.facebook.com/v19.0/{PHONE_ID}/messages",
            data=data,
            headers={
                "Authorization": f"Bearer {META_TOKEN}",
                "Content-Type": "application/json"
            }
        )
        with _ur.urlopen(req) as r:
            resp = json.loads(r.read())
            print(f"Meta WA enviado OK: {resp.get('messages', [{}])[0].get('id','?')}")
    except Exception as e:
        print(f"Error WA Meta: {e}")

def generar_resumen(datos):
    ops  = datos.get('operarios', 'Ninguno')
    albs = datos.get('albaranes', 'Ninguno')
    mat  = datos.get('material_stock', 'Ninguno')
    desc = datos.get('descripcion', '-')
    term = datos.get('terminado', '-')
    trem = datos.get('tiempo_restante', '')
    linea_term = f"✅ Sí" if normalizar(term) in ['si','sí'] else f"🔄 No — {trem}" if trem else f"🔄 No"
    return (
        f"📋 *RESUMEN DEL PARTE*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 Fecha: {datos['fecha']}\n"
        f"🏢 Cliente: {datos['cliente']}\n"
        f"🔨 Obra: {datos['obra']}\n"
        f"👷 Operarios:\n{ops}\n"
        f"📦 Albaranes: {albs}\n"
        f"🏗️ Material stock: {mat}\n"
        f"📝 Descripción: {desc}\n"
        f"🏁 Terminado: {linea_term}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"¿Es correcto? Responde *SÍ* para enviar o *NO* para cancelar."
    )

def finalizar_parte(numero, datos):
    ops  = datos.get('operarios', 'Ninguno')
    albs = datos.get('albaranes', 'Ninguno')
    mat  = datos.get('material_stock', 'Ninguno')
    desc = datos.get('descripcion', '-')
    term = datos.get('terminado', '-')
    trem = datos.get('tiempo_restante', '')
    linea_term = f"Sí" if normalizar(term) in ['si','sí'] else f"No — {trem}" if trem else "No"

    msg_supervisor = (
        f"📋 *PARTE DE TRABAJO*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 {datos['fecha']}\n"
        f"📱 Operario: {nombre_operario(numero)}\n"
        f"🏢 Cliente: {datos['cliente']}\n"
        f"🔨 Obra: {datos['obra']}\n"
        f"👷 Operarios:\n{ops}\n"
        f"📦 Albaranes: {albs}\n"
        f"🏗️ Material stock: {mat}\n"
        f"📝 {desc}\n"
        f"🏁 Terminado: {linea_term}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    enviar_whatsapp(SUPERVISOR_WA, msg_supervisor)

    # Guardar en base de datos y obtener el ID para la URL del PDF
    parte_id = guardar_parte(datos, numero)

    # Enviar PDF a tu WhatsApp y al operario
    BOT_URL = os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'bot-production-66b8.up.railway.app')
    if parte_id:
        pdf_url = f"https://{BOT_URL}/partes/{parte_id}/pdf"
        import unicodedata as _ud
        def _clean(t):
            t2 = _ud.normalize('NFD', str(t))
            return ''.join(c for c in t2 if _ud.category(c) != 'Mn')
        caption = (
            f"📄 *Parte* — {datos['fecha']}\n"
            f"🏢 {_clean(datos['cliente'])} | 🔨 {_clean(datos['obra'])}\n"
            f"🏁 {linea_term}"
        )
        enviar_whatsapp(SUPERVISOR_WA, caption, media_url=pdf_url)
        operario_wa = f"whatsapp:{numero}" if not numero.startswith("whatsapp:") else numero
        enviar_whatsapp(operario_wa,
                        f"✅ *Parte confirmado*\nAquí tienes tu copia en PDF:",
                        media_url=pdf_url)

    borrar_estado(numero)


MENSAJES_VEHICULO = ['vehiculo', 'vehículo', 'coche', 'camion', 'camión', 'furgoneta', 'mantenimiento vehiculo']

INIT_VEHICULOS_SQL = """
    CREATE TABLE IF NOT EXISTS vehiculos (
        id SERIAL PRIMARY KEY,
        matricula VARCHAR(20),
        marca_modelo VARCHAR(100),
        mes VARCHAR(20),
        km_inicio VARCHAR(20),
        km_fin VARCHAR(20),
        proximo_aceite VARCHAR(20),
        estado_neumaticos TEXT,
        conductores TEXT,
        mantenimientos TEXT,
        observaciones TEXT,
        golpes TEXT,
        operario VARCHAR(100),
        created_at TIMESTAMP DEFAULT NOW(),
        pdf_descargado BOOLEAN DEFAULT FALSE,
        pdf_descargado_at TIMESTAMP
    )
"""

def iniciar_vehiculo(numero):
    try:
        conn = get_db(); cur = conn.cursor()
        datos = json.dumps({
            'tipo': 'vehiculo',
            'operario': numero,
            'matricula': '', 'marca_modelo': '', 'mes': '',
            'km_inicio': '', 'km_fin': '', 'proximo_aceite': '',
            'estado_neumaticos': '', 'conductores': '',
            'mantenimientos': '', 'observaciones': '', 'golpes': '',
            'fecha': datetime.now().strftime('%d/%m/%Y')
        })
        cur.execute("""
            INSERT INTO conversaciones_db (numero, paso, datos, updated_at)
            VALUES (%s, 'v_matricula', %s::jsonb, NOW())
            ON CONFLICT (numero) DO UPDATE SET paso='v_matricula', datos=%s::jsonb, updated_at=NOW()
        """, (numero, datos, datos))
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        print(f"Error iniciar_vehiculo: {e}")

def guardar_vehiculo(datos, numero_operario):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            INSERT INTO vehiculos (matricula, marca_modelo, mes, km_inicio, km_fin,
                proximo_aceite, estado_neumaticos, conductores, mantenimientos,
                observaciones, golpes, operario)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        """, (
            datos.get('matricula',''), datos.get('marca_modelo',''),
            datos.get('mes',''), datos.get('km_inicio',''), datos.get('km_fin',''),
            datos.get('proximo_aceite',''), datos.get('estado_neumaticos',''),
            datos.get('conductores',''), datos.get('mantenimientos',''),
            datos.get('observaciones',''), datos.get('golpes',''),
            numero_operario
        ))
        vid = cur.fetchone()[0]
        conn.commit(); cur.close(); conn.close()
        return vid
    except Exception as e:
        print(f"Error guardar_vehiculo: {e}")
        return None

def generar_pdf_vehiculo(datos):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    elements = []
    AZUL  = colors.HexColor('#1a3a5c')
    GRIS  = colors.HexColor('#f5f5f5')
    titulo_style = ParagraphStyle('t', fontSize=16, textColor=AZUL, alignment=TA_CENTER, fontName='Helvetica-Bold')
    sec_style    = ParagraphStyle('s', fontSize=9,  textColor=colors.white, backColor=AZUL,
                                  fontName='Helvetica-Bold', spaceAfter=0, spaceBefore=6, borderPad=4)
    pie_style    = ParagraphStyle('p', fontSize=7,  textColor=colors.grey, alignment=TA_CENTER)
    import os as _os
    LOGO_PATH = _os.path.join(_os.path.dirname(__file__), 'logo.jpg')
    if _os.path.exists(LOGO_PATH):
        _lw = 4*cm; _lh = _lw / (1024/219)
        logo_img = RLImage(LOGO_PATH, width=_lw, height=_lh)
    else:
        logo_img = Paragraph("INSTAPALMA", titulo_style)
    cab_t = ParagraphStyle('ct', fontName='Helvetica-Bold', fontSize=16, textColor=AZUL, alignment=1, leading=20)
    cab = Table([[logo_img, Paragraph('PARTE DE VEHÍCULO', cab_t)]], colWidths=[5*cm, 12*cm])
    cab.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0)]))
    elements.append(cab)
    elements.append(HRFlowable(width="100%", thickness=2, color=AZUL, spaceAfter=6))
    elements.append(Spacer(1, 0.2*cm))

    t_gen = Table([
        ['Matrícula', datos.get('matricula',''), 'Mes', datos.get('mes','')],
        ['Marca/Modelo', datos.get('marca_modelo',''), 'Fecha', datos.get('fecha','')],
    ], colWidths=[3*cm, 6.5*cm, 2.5*cm, 5*cm])
    t_gen.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(0,-1),AZUL),('BACKGROUND',(2,0),(2,-1),AZUL),
        ('TEXTCOLOR',(0,0),(0,-1),colors.white),('TEXTCOLOR',(2,0),(2,-1),colors.white),
        ('FONTNAME',(0,0),(0,-1),'Helvetica-Bold'),('FONTNAME',(2,0),(2,-1),'Helvetica-Bold'),
        ('FONTSIZE',(0,0),(-1,-1),9),
        ('GRID',(0,0),(-1,-1),0.5,colors.lightgrey),('PADDING',(0,0),(-1,-1),6),
    ]))
    elements.append(t_gen)
    elements.append(Spacer(1,0.3*cm))

    elements.append(Paragraph("KILÓMETROS", sec_style))
    t_km = Table([
        ['Km inicio mes', datos.get('km_inicio',''), 'Km fin mes', datos.get('km_fin','')],
        ['Próximo cambio aceite (km)', datos.get('proximo_aceite',''), '', ''],
    ], colWidths=[5*cm, 4.5*cm, 4*cm, 3.5*cm])
    t_km.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(0,-1),GRIS),('BACKGROUND',(2,0),(2,-1),GRIS),
        ('FONTNAME',(0,0),(0,-1),'Helvetica-Bold'),('FONTNAME',(2,0),(2,-1),'Helvetica-Bold'),
        ('FONTSIZE',(0,0),(-1,-1),9),
        ('GRID',(0,0),(-1,-1),0.5,colors.lightgrey),('PADDING',(0,0),(-1,-1),6),
        ('SPAN',(1,1),(3,1)),
    ]))
    elements.append(t_km)
    elements.append(Spacer(1,0.3*cm))

    elements.append(Paragraph("ESTADO NEUMÁTICOS", sec_style))
    t_neum = Table([[datos.get('estado_neumaticos','Ninguno')]], colWidths=[17*cm])
    t_neum.setStyle(TableStyle([
        ('FONTSIZE',(0,0),(-1,-1),9),('GRID',(0,0),(-1,-1),0.5,colors.lightgrey),
        ('PADDING',(0,0),(-1,-1),6),('BACKGROUND',(0,0),(-1,-1),GRIS),
    ]))
    elements.append(t_neum)
    elements.append(Spacer(1,0.3*cm))

    elements.append(Paragraph("CONDUCTORES", sec_style))
    cond_rows = [['Conductor', 'Desde', 'Hasta']]
    for linea in (datos.get('conductores','') or '').split('\n'):
        linea = linea.strip()
        if not linea: continue
        partes = [p.strip() for p in linea.replace('—','-').split('-',2)]
        if len(partes) >= 3:
            cond_rows.append([partes[0], partes[1], partes[2]])
        elif len(partes) == 2:
            cond_rows.append([partes[0], partes[1], ''])
        else:
            cond_rows.append([linea, '', ''])
    t_cond = Table(cond_rows, colWidths=[8*cm, 4.5*cm, 4.5*cm])
    t_cond.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),AZUL),('TEXTCOLOR',(0,0),(-1,0),colors.white),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),9),
        ('GRID',(0,0),(-1,-1),0.5,colors.lightgrey),('PADDING',(0,0),(-1,-1),6),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,GRIS]),
    ]))
    elements.append(t_cond)
    elements.append(Spacer(1,0.3*cm))

    elements.append(Paragraph("MANTENIMIENTOS REALIZADOS", sec_style))
    mant_rows = [['Concepto', 'Fecha', 'Km']]
    for linea in (datos.get('mantenimientos','') or '').split('\n'):
        linea = linea.strip()
        if not linea: continue
        partes = [p.strip() for p in linea.replace('—','-').split('-',2)]
        if len(partes) >= 3:
            mant_rows.append([partes[0], partes[1], partes[2]])
        elif len(partes) == 2:
            mant_rows.append([partes[0], partes[1], ''])
        else:
            mant_rows.append([linea, '', ''])
    t_mant = Table(mant_rows, colWidths=[9*cm, 4*cm, 4*cm])
    t_mant.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),AZUL),('TEXTCOLOR',(0,0),(-1,0),colors.white),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),9),
        ('GRID',(0,0),(-1,-1),0.5,colors.lightgrey),('PADDING',(0,0),(-1,-1),6),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,GRIS]),
    ]))
    elements.append(t_mant)
    elements.append(Spacer(1,0.3*cm))

    elements.append(Paragraph("OBSERVACIONES / PRÓXIMOS MANTENIMIENTOS", sec_style))
    t_obs = Table([[datos.get('observaciones','Ninguna')]], colWidths=[17*cm])
    t_obs.setStyle(TableStyle([
        ('FONTSIZE',(0,0),(-1,-1),9),('GRID',(0,0),(-1,-1),0.5,colors.lightgrey),
        ('PADDING',(0,0),(-1,-1),6),
    ]))
    elements.append(t_obs)
    elements.append(Spacer(1,0.3*cm))

    elements.append(Paragraph("GOLPES Y DESPERFECTOS", sec_style))
    t_golp = Table([[datos.get('golpes','Ninguno')]], colWidths=[17*cm])
    t_golp.setStyle(TableStyle([
        ('FONTSIZE',(0,0),(-1,-1),9),('GRID',(0,0),(-1,-1),0.5,colors.lightgrey),
        ('PADDING',(0,0),(-1,-1),6),('BACKGROUND',(0,0),(-1,-1),GRIS),
    ]))
    elements.append(t_golp)
    elements.append(Spacer(1,0.5*cm))

    elements.append(HRFlowable(width="100%", thickness=1, color=colors.lightgrey, spaceAfter=4))
    elements.append(Paragraph("Instapalma — Parte de Vehículo generado automáticamente", pie_style))
    doc.build(elements)
    return buffer.getvalue()

def generar_resumen_vehiculo(datos):
    return (
        f"📋 *Resumen — Parte de Vehículo*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🚗 {datos.get('matricula','')} | {datos.get('marca_modelo','')}\n"
        f"📅 Mes: {datos.get('mes','')}\n"
        f"📍 Km inicio: {datos.get('km_inicio','')} | Km fin: {datos.get('km_fin','')}\n"
        f"🔧 Próx. aceite: {datos.get('proximo_aceite','')} km\n"
        f"🔴 Neumáticos: {datos.get('estado_neumaticos','')}\n"
        f"👤 Conductores:\n{datos.get('conductores','')}\n"
        f"🛠️ Mantenimientos:\n{datos.get('mantenimientos','')}\n"
        f"📝 Observaciones: {datos.get('observaciones','')}\n"
        f"⚠️ Golpes: {datos.get('golpes','')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"¿Es correcto? Responde *SÍ* para enviar o *NO* para cancelar."
    )

def finalizar_vehiculo(numero, datos):
    mat = datos.get('matricula','').replace(' ','_').upper()
    mes = datos.get('mes','').replace('/','_').replace(' ','_')
    nombre_pdf = f"{mes}-{mat}-VEHICULO.pdf"
    pdf_bytes = generar_pdf_vehiculo(datos)

    try:
        msg_email = MIMEMultipart()
        msg_email['From']    = GMAIL_USER
        msg_email['To']      = ', '.join([SUPERVISOR_EMAIL_1, SUPERVISOR_EMAIL_2])
        msg_email['Subject'] = f"Parte Vehiculo - {mat} - {mes}"
        body_txt = (
            f"Parte de vehiculo generado.\n\n"
            f"Matricula: {datos.get('matricula','')}\n"
            f"Modelo: {datos.get('marca_modelo','')}\n"
            f"Mes: {mes}\n"
            f"Km inicio: {datos.get('km_inicio','')} | Km fin: {datos.get('km_fin','')}\n"
        )
        msg_email.attach(MIMEText(body_txt, 'plain'))
        part = MIMEApplication(pdf_bytes, Name=nombre_pdf)
        part.add_header('Content-Disposition', f'attachment; filename="{nombre_pdf}"')
        msg_email.attach(part)
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as srv:
            srv.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            srv.sendmail(GMAIL_USER, [SUPERVISOR_EMAIL_1, SUPERVISOR_EMAIL_2], msg_email.as_string())
        print("Email vehiculo OK")
    except Exception as e:
        print(f"Error email vehiculo: {e}")

    vid = guardar_vehiculo(datos, numero)
    BOT_URL = os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'bot-production-66b8.up.railway.app')
    if vid:
        pdf_url = f"https://{BOT_URL}/vehiculos/{vid}/pdf"
        caption = f"Parte Vehiculo - {mat} - {mes}\nKm: {datos.get('km_inicio','')} a {datos.get('km_fin','')}"
        enviar_whatsapp(SUPERVISOR_WA,   caption, media_url=pdf_url)
        op_wa = f"whatsapp:{numero}" if not numero.startswith("whatsapp:") else numero
        enviar_whatsapp(op_wa, "Parte de vehiculo enviado. Aqui tienes tu copia:", media_url=pdf_url)

    borrar_estado(numero)


@app.route('/webhook', methods=['GET'])
def webhook_verify():
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')
    if mode == 'subscribe' and token == 'instapalma2024':
        return challenge, 200
    return 'Forbidden', 403

@app.route('/webhook', methods=['POST'])
def webhook():
    # Detectar si es Meta Cloud API (JSON) o Twilio (form)
    if request.is_json:
        data = request.get_json()
        try:
            entry = data['entry'][0]['changes'][0]['value']
            msg_obj = entry['messages'][0]
            incoming_msg = msg_obj.get('text', {}).get('body', '').strip()
            numero = 'whatsapp:+' + msg_obj['from']
        except (KeyError, IndexError):
            return 'OK', 200
    else:
        incoming_msg = request.form.get('Body', '').strip()
        numero = request.form.get('From', '')

    use_meta = request.is_json

    class MetaMsg:
        def __init__(self):
            self._body = None
        def body(self, text):
            self._body = text
            enviar_whatsapp(numero, text)

    class MetaResp:
        def __init__(self):
            self._msg = MetaMsg()
        def message(self):
            return self._msg
        def __str__(self):
            return ''

    if use_meta:
        resp = MetaResp()
    else:
        resp = MessagingResponse()
    msg = resp.message()
    estado = get_estado(numero)

    # Comando reset en cualquier momento
    if normalizar(incoming_msg) in ['reset', 'reiniciar', 'restart']:
        borrar_estado(numero)
        msg.body("🔄 Conversación reiniciada. Escribe *parte* para comenzar de nuevo.")
        return str(resp)

    # Detectar arranque vehiculo
    if any(p in normalizar(incoming_msg) for p in MENSAJES_VEHICULO):
        if not estado or estado.get('datos', {}).get('tipo') != 'vehiculo':
            iniciar_vehiculo(numero)
            msg.body(
                "🚗 *Bot de Vehículos — Instapalma*\n\n"
                "Vamos a registrar el parte mensual paso a paso.\n\n"
                "1️⃣ ¿Cuál es la *matrícula* del vehículo?"
            )
            return str(resp)

    if not estado:
        if any(p in normalizar(incoming_msg) for p in MENSAJES_INICIO):
            iniciar_parte(numero)
            msg.body(
                "👷 *Bot de Partes de Trabajo — Instapalma*\n\n"
                "Vamos a crear tu parte paso a paso.\n\n"
                "1️⃣ ¿Cuál es el *cliente*?"
            )
        else:
            msg.body("Hola 👋 Para crear un parte de trabajo escribe: *parte*")
        return str(resp)

    paso = estado['paso']
    datos = estado['datos']

    if paso == 'cliente':
        set_dato(numero, 'cliente', incoming_msg.upper())
        set_paso(numero, 'obra')
        msg.body("2️⃣ ¿Cuál es la *obra*?")

    elif paso == 'obra':
        set_dato(numero, 'obra', incoming_msg.upper())
        set_paso(numero, 'operarios')
        msg.body(
            "3️⃣ *Operarios y horas*\n\n"
            "Escribe cada operario en una línea:\n"
            "_Ejemplo:_\n"
            "JORGE GARCIA — 8h\n"
            "ANTONIO — 6h"
        )

    elif paso == 'operarios':
        import re
        lineas = [l.strip() for l in incoming_msg.strip().split('\n') if l.strip()]
        errores = []
        for linea in lineas:
            # Debe tener texto (nombre) Y número de horas
            tiene_nombre = bool(re.search(r'[a-záéíóúüñA-ZÁÉÍÓÚÜÑ]', linea))
            tiene_horas  = bool(re.search(r'\d+[\.,]?\d*\s*h(?:oras?|rs?)?', linea, re.IGNORECASE))
            if not tiene_nombre or not tiene_horas:
                errores.append(linea)
        if not lineas:
            msg.body(
                "⚠️ No has escrito ningún operario.\n\n"
                "Escribe uno por línea:\n"
                "_NOMBRE — 8h_"
            )
        elif errores:
            lista = '\n'.join(f'• {l}' for l in errores)
            msg.body(
                f"⚠️ Formato incorrecto en:\n{lista}\n\n"
                "Cada línea debe tener *nombre* y *horas*:\n"
                "_JUAN GARCÍA — 8h_\n"
                "_ANTONIO — 6.5h_\n\n"
                "Escribe de nuevo la lista completa:"
            )
        else:
            set_dato(numero, 'operarios', incoming_msg)
            set_paso(numero, 'albaranes')
            msg.body(
                "4️⃣ *Albaranes*\n\n"
                "Escribe los albaranes como quieras, uno por línea.\n"
                "_Ejemplo:_\n"
                "DIEXFE 012604\n"
                "COELCA 9981\n\n"
                "Si no hay, escribe: *ninguno*"
            )

    elif paso == 'albaranes':
        if normalizar(incoming_msg) in ['ninguno', 'no', 'n']:
            set_dato(numero, 'albaranes', 'Ninguno')
        else:
            set_dato(numero, 'albaranes', incoming_msg)
        set_paso(numero, 'material_stock')
        msg.body(
            "5️⃣ *Material de stock* utilizado\n\n"
            "Escribe el material, uno por línea:\n"
            "_Ejemplo:_\n"
            "Cable 2.5mm² — 20m\n"
            "Caja superficie — 2ud\n\n"
            "Si no hay, escribe: *ninguno*"
        )

    elif paso == 'material_stock':
        val = incoming_msg if normalizar(incoming_msg) != 'ninguno' else 'Ninguno'
        set_dato(numero, 'material_stock', val)
        set_paso(numero, 'descripcion')
        msg.body("6️⃣ *Descripción* de los trabajos realizados:")

    elif paso == 'descripcion':
        set_dato(numero, 'descripcion', incoming_msg)
        set_paso(numero, 'terminado')
        msg.body("7️⃣ ¿El trabajo está *terminado*?\n\nResponde *SÍ* o *NO*")

    elif paso == 'terminado':
        if normalizar(incoming_msg) in ['si', 'sí', 's', 'yes']:
            set_dato(numero, 'terminado', 'Sí')
            set_dato(numero, 'tiempo_restante', '')
            set_paso(numero, 'confirmar')
            msg.body(generar_resumen(get_estado(numero)['datos']))
        elif normalizar(incoming_msg) in ['no', 'n']:
            set_dato(numero, 'terminado', 'No')
            set_paso(numero, 'tiempo_restante')
            msg.body("8️⃣ ¿Cuánto tiempo queda para terminarlo?\n\n_Ejemplo: 2 días, media jornada, 3 horas..._")
        else:
            msg.body("Responde *SÍ* si está terminado o *NO* si falta trabajo.")

    elif paso == 'tiempo_restante':
        set_dato(numero, 'tiempo_restante', incoming_msg)
        set_paso(numero, 'confirmar')
        msg.body(generar_resumen(get_estado(numero)['datos']))

    elif paso == 'confirmar':
        if es_confirmacion(incoming_msg):
            finalizar_parte(numero, datos)
            msg.body(
                "✅ *Parte enviado correctamente.*\n\n"
                "Se ha notificado al supervisor por WhatsApp y email con PDF. ¡Gracias!"
            )
        elif es_cancelacion(incoming_msg):
            borrar_estado(numero)
            msg.body("❌ Parte cancelado. Escribe *parte* para crear uno nuevo.")
        else:
            msg.body("Responde *SÍ* para confirmar y enviar, o *NO* para cancelar.")

    # ── Flujo vehículos ────────────────────────────────────────────────────
    elif paso == 'v_matricula':
        set_dato(numero, 'matricula', incoming_msg.upper())
        set_paso(numero, 'v_modelo')
        msg.body("2️⃣ *Marca y modelo* del vehículo:\n_Ejemplo: Ford Transit 2020_")

    elif paso == 'v_modelo':
        set_dato(numero, 'marca_modelo', incoming_msg)
        set_paso(numero, 'v_mes')
        msg.body("3️⃣ ¿A qué *mes* corresponde este parte?\n_Ejemplo: Junio 2026_")

    elif paso == 'v_mes':
        set_dato(numero, 'mes', incoming_msg)
        set_paso(numero, 'v_km_inicio')
        msg.body("4️⃣ *Km al inicio del mes*:")

    elif paso == 'v_km_inicio':
        set_dato(numero, 'km_inicio', incoming_msg)
        set_paso(numero, 'v_km_fin')
        msg.body("5️⃣ *Km al final del mes*:")

    elif paso == 'v_km_fin':
        set_dato(numero, 'km_fin', incoming_msg)
        set_paso(numero, 'v_aceite')
        msg.body("6️⃣ ¿A cuántos km es el *próximo cambio de aceite*?\n_Ejemplo: 85000_")

    elif paso == 'v_aceite':
        set_dato(numero, 'proximo_aceite', incoming_msg)
        set_paso(numero, 'v_neumaticos')
        msg.body(
            "7️⃣ *Estado de los neumáticos*\n\n"
            "_Ejemplo: Delanteros OK, traseros con desgaste_\n"
            "Si están bien, escribe: *OK*"
        )

    elif paso == 'v_neumaticos':
        set_dato(numero, 'estado_neumaticos', incoming_msg)
        set_paso(numero, 'v_conductores')
        msg.body(
            "8️⃣ *Conductores del mes*\n\n"
            "Escribe uno por línea con fechas:\n"
            "_NOMBRE - desde - hasta_\n"
            "Ejemplo:\n"
            "JUAN GARCÍA - 01/06 - 15/06\n"
            "PEDRO MARTÍN - 16/06 - 30/06\n\n"
            "Si solo hay uno todo el mes escribe el nombre."
        )

    elif paso == 'v_conductores':
        set_dato(numero, 'conductores', incoming_msg)
        set_paso(numero, 'v_mantenimientos')
        msg.body(
            "9️⃣ *Mantenimientos realizados*\n\n"
            "Escribe uno por línea:\n"
            "_CONCEPTO - FECHA - KM_\n"
            "Ejemplo:\n"
            "Cambio aceite - 10/06/2026 - 82000\n"
            "Revisión frenos - 20/06/2026 - 82500\n\n"
            "Si no hay, escribe: *ninguno*"
        )

    elif paso == 'v_mantenimientos':
        val = incoming_msg if normalizar(incoming_msg) != 'ninguno' else 'Ninguno'
        set_dato(numero, 'mantenimientos', val)
        set_paso(numero, 'v_observaciones')
        msg.body(
            "🔟 *Observaciones y próximos mantenimientos*\n\n"
            "Escribe lo que necesites.\n"
            "Si no hay, escribe: *ninguno*"
        )

    elif paso == 'v_observaciones':
        val = incoming_msg if normalizar(incoming_msg) != 'ninguno' else 'Ninguno'
        set_dato(numero, 'observaciones', val)
        set_paso(numero, 'v_golpes')
        msg.body(
            "1️⃣1️⃣ *Golpes y desperfectos*\n\n"
            "Describe cualquier daño visible.\n"
            "Si no hay, escribe: *ninguno*"
        )

    elif paso == 'v_golpes':
        val = incoming_msg if normalizar(incoming_msg) != 'ninguno' else 'Ninguno'
        set_dato(numero, 'golpes', val)
        set_paso(numero, 'v_confirmar')
        msg.body(generar_resumen_vehiculo(get_estado(numero)['datos']))

    elif paso == 'v_confirmar':
        if es_confirmacion(incoming_msg):
            finalizar_vehiculo(numero, datos)
            msg.body("✅ *Parte de vehículo enviado.* Se ha notificado al supervisor por WhatsApp y email. ¡Gracias!")
        elif es_cancelacion(incoming_msg):
            borrar_estado(numero)
            msg.body("❌ Parte cancelado. Escribe *vehiculo* para crear uno nuevo.")
        else:
            msg.body("Responde *SÍ* para confirmar y enviar, o *NO* para cancelar.")

    if use_meta:
        return 'OK', 200
    return str(resp)

CSS_BASE = """
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f0f2f5; color: #333; }
  header { background: #1a3a5c; color: white; padding: 20px 30px; display: flex; align-items: center; justify-content: space-between; }
  header h1 { font-size: 22px; font-weight: 700; }
  header p { font-size: 13px; opacity: .75; margin-top: 2px; }
  .stats { display: flex; gap: 16px; padding: 20px 30px; flex-wrap: wrap; }
  .stat { background: white; border-radius: 10px; padding: 16px 24px; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
  .stat .num { font-size: 28px; font-weight: 700; color: #1a3a5c; }
  .stat .lbl { font-size: 12px; color: #888; margin-top: 2px; }
  .wrap { padding: 0 30px 30px; overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; background: white; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,.08); font-size: 13px; }
  th { background: #1a3a5c; color: white; padding: 12px 10px; text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: .5px; white-space: nowrap; }
  td { padding: 10px; border-bottom: 1px solid #f0f0f0; vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  tr.clickable:hover td { background: #eef3fa; cursor: pointer; }
  .badge-ok { background:#2e7d32; color:white; padding:3px 10px; border-radius:10px; font-size:11px; white-space:nowrap; }
  .badge-curso { background:#e65100; color:white; padding:3px 10px; border-radius:10px; font-size:11px; white-space:nowrap; }
  .empty { text-align: center; padding: 60px; color: #aaa; font-size: 15px; }
  .back { display:inline-block; margin:20px 30px 0; color:#1a3a5c; text-decoration:none; font-weight:600; font-size:14px; }
  .back:hover { text-decoration:underline; }
  .ficha { max-width: 800px; margin: 24px auto; background: white; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,.1); overflow: hidden; }
  .ficha-header { background: #1a3a5c; color: white; padding: 20px 28px; }
  .ficha-header h2 { font-size: 20px; }
  .ficha-header p { font-size: 13px; opacity: .75; margin-top: 4px; }
  .ficha-body { padding: 28px; }
  .campo { margin-bottom: 18px; }
  .campo label { display:block; font-size:11px; text-transform:uppercase; letter-spacing:.5px; color:#888; margin-bottom:4px; }
  .campo .val { font-size:15px; color:#222; white-space:pre-line; }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
  .estado-ok { background:#e8f5e9; border:1px solid #a5d6a7; border-radius:8px; padding:14px 20px; text-align:center; font-weight:700; color:#2e7d32; font-size:16px; }
  .estado-curso { background:#fff3e0; border:1px solid #ffcc80; border-radius:8px; padding:14px 20px; text-align:center; font-weight:700; color:#e65100; font-size:16px; }
  .btn-pdf { display:inline-block; margin-top:20px; background:#1a3a5c; color:white; padding:10px 22px; border-radius:8px; text-decoration:none; font-size:14px; font-weight:600; }
  .btn-pdf:hover { background:#14304f; }
  @media (max-width:600px) { .grid2 { grid-template-columns:1fr; } .wrap { padding:0 12px 20px; } header h1 { font-size:17px; } }
"""

def get_parte_by_id(parte_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, numero_parte, fecha, operario, cliente, obra, operarios, albaranes, material_stock, descripcion, terminado, tiempo_restante, created_at FROM partes WHERE id=%s", (parte_id,))
        r = cur.fetchone()
        cur.close(); conn.close()
        return r
    except:
        return None

@app.route('/partes', methods=['GET'])
def listar_partes():
    if request.args.get('format') == 'json':
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT id, numero_parte, fecha, operario, cliente, obra, operarios, albaranes, material_stock, descripcion, terminado, tiempo_restante, created_at FROM partes ORDER BY created_at DESC LIMIT 200")
            rows = cur.fetchall()
            cur.close(); conn.close()
            partes = [{'id':r[0],'numero_parte':r[1],'fecha':r[2],'operario':r[3],'cliente':r[4],'obra':r[5],'operarios':r[6],'albaranes':r[7],'material_stock':r[8],'descripcion':r[9],'terminado':r[10],'tiempo_restante':r[11],'created_at':str(r[12])} for r in rows]
            return {'partes': partes, 'total': len(partes)}, 200
        except Exception as e:
            return {'error': str(e)}, 500

    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, numero_parte, fecha, operario, cliente, obra, terminado, tiempo_restante, created_at, pdf_descargado, pdf_descargado_at FROM partes ORDER BY created_at DESC LIMIT 200")
        rows = cur.fetchall()
        cur.close(); conn.close()
    except:
        rows = []

    filas = ""
    for r in rows:
        terminado = r[6] or ''
        es_ok = 'í' in terminado.lower() or terminado.lower() == 'si'
        badge = '<span class="badge-ok">✓ Terminado</span>' if es_ok else f'<span class="badge-curso">🔄 {r[7] or "En curso"}</span>'
        operario_limpio = nombre_operario(r[3] or '')
        descargado = r[9]
        desc_at = str(r[10])[:16].replace('T',' ') if r[10] else ''
        if descargado:
            pdf_badge = f'<span title="Descargado {desc_at}" style="color:#2e7d32;font-size:18px" title="{desc_at}">⬇️</span>'
        else:
            pdf_badge = '<span style="color:#ccc;font-size:18px">—</span>'
        filas += f'<tr class="clickable" onclick="window.location=\'/partes/{r[0]}\'">' \
                 f'<td>{r[2] or ""}</td>' \
                 f'<td style="font-size:11px;color:#666">{operario_limpio}</td>' \
                 f'<td><strong>{r[4] or ""}</strong></td>' \
                 f'<td>{r[5] or ""}</td>' \
                 f'<td>{badge}</td>' \
                 f'<td style="text-align:center">{pdf_badge}</td>' \
                 f'</tr>'

    total = len(rows)
    n_ok = sum(1 for r in rows if r[6] and ('í' in r[6].lower() or r[6].lower()=='si'))
    n_curso = sum(1 for r in rows if r[6] and 'no' in r[6].lower())
    n_pdf = sum(1 for r in rows if r[9])

    tabla = "<p class='empty'>No hay partes registrados aún.</p>" if not rows else f"""
  <table>
    <thead><tr>
      <th>Fecha</th><th>Operario</th><th>Cliente</th><th>Obra</th><th>Estado</th><th>PDF</th>
    </tr></thead>
    <tbody>{filas}</tbody>
  </table>"""

    html = f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Partes de Trabajo — Instapalma</title>
<style>{CSS_BASE}</style>
</head>
<body>
<header>
  <div><h1>⚡ Partes de Trabajo — Instapalma</h1><p>Panel de control · Haz clic en un parte para ver el detalle</p></div>
</header>
<div class="stats">
  <div class="stat"><div class="num">{total}</div><div class="lbl">Total partes</div></div>
  <div class="stat"><div class="num">{n_ok}</div><div class="lbl">Terminados</div></div>
  <div class="stat"><div class="num">{n_curso}</div><div class="lbl">En curso</div></div>
  <div class="stat"><div class="num">{n_pdf}</div><div class="lbl">PDFs descargados</div></div>
</div>
<div class="wrap">{tabla}</div>
</body></html>"""
    return html, 200, {'Content-Type': 'text/html; charset=utf-8'}


@app.route('/partes/<int:parte_id>', methods=['GET'])
def ver_parte(parte_id):
    r = get_parte_by_id(parte_id)
    if not r:
        return "<p style='padding:40px;font-family:sans-serif'>Parte no encontrado.</p>", 404

    terminado = r[10] or ''
    es_ok = 'í' in terminado.lower() or terminado.lower() == 'si'
    estado_html = f'<div class="estado-ok">✅ TRABAJO TERMINADO</div>' if es_ok \
        else f'<div class="estado-curso">🔄 EN CURSO — Tiempo restante: {r[11] or "no especificado"}</div>'
    operario_limpio = nombre_operario(r[3] or '')

    html = f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Parte {r[1]} — Instapalma</title>
<style>{CSS_BASE}</style>
</head>
<body>
<header>
  <div><h1>⚡ Instapalma — Ficha de Parte</h1><p>Detalle completo del parte de trabajo</p></div>
</header>
<a class="back" href="/partes">← Volver al listado</a>
<div class="ficha">
  <div class="ficha-header">
    <h2>Parte — {r[2] or ''}</h2>
    <p>Fecha: {r[2] or '—'} &nbsp;·&nbsp; Registrado: {str(r[12])[:16] if r[12] else '—'}</p>
  </div>
  <div class="ficha-body">
    <div class="grid2">
      <div class="campo"><label>Cliente</label><div class="val">{r[4] or '—'}</div></div>
      <div class="campo"><label>Obra</label><div class="val">{r[5] or '—'}</div></div>
      <div class="campo"><label>Operario</label><div class="val">{operario_limpio}</div></div>
    </div>
    <div class="campo"><label>Operarios y horas</label><div class="val">{r[6] or '—'}</div></div>
    <div class="campo"><label>Albaranes</label><div class="val">{r[7] or '—'}</div></div>
    <div class="campo"><label>Material de stock</label><div class="val">{r[8] or '—'}</div></div>
    <div class="campo"><label>Descripción de trabajos</label><div class="val">{r[9] or '—'}</div></div>
    {estado_html}
    <a class="btn-pdf" href="/partes/{parte_id}/pdf">⬇ Descargar PDF</a>
  </div>
</div>
</body></html>"""
    return html, 200, {'Content-Type': 'text/html; charset=utf-8'}


@app.route('/partes/<int:parte_id>/pdf', methods=['GET'])
def descargar_pdf(parte_id):
    r = get_parte_by_id(parte_id)
    if not r:
        return "Parte no encontrado", 404
    datos = {
        'numero_parte': r[1], 'fecha': r[2], 'cliente': r[4], 'obra': r[5],
        'operarios': r[6] or '', 'albaranes': r[7] or '', 'material_stock': r[8] or '',
        'descripcion': r[9] or '', 'terminado': r[10] or '', 'tiempo_restante': r[11] or ''
    }
    pdf_bytes = generar_pdf(datos)
    fecha_pdf2 = (r[2] or '').replace('/', '-').replace(' ', '')
    obra_pdf2 = limpiar_nombre_archivo(r[5] or 'obra')
    ops_raw2 = r[6] or ''
    ops_lista2 = [limpiar_nombre_archivo(l.split('—')[0].split('-')[0].strip().split()[0]) for l in ops_raw2.split('\n') if l.strip()]
    ops_pdf2 = '-'.join(ops_lista2) if ops_lista2 else 'OPERARIOS'
    nombre = f"{fecha_pdf2}-{obra_pdf2}-{ops_pdf2}.pdf"
    # Marcar como descargado
    try:
        from datetime import datetime as dt
        conn2 = get_db(); cur2 = conn2.cursor()
        cur2.execute("UPDATE partes SET pdf_descargado=TRUE, pdf_descargado_at=%s WHERE id=%s",
                    (dt.utcnow(), parte_id))
        conn2.commit(); cur2.close(); conn2.close()
    except Exception:
        pass
    from flask import Response
    return Response(pdf_bytes, mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{nombre}"'})

@app.route('/admin/insert', methods=['POST'])
def admin_insert():
    try:
        datos = request.get_json()
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO partes (numero_parte, fecha, operario, cliente, obra, operarios, albaranes, material_stock, descripcion, terminado, tiempo_restante)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            datos.get('numero_parte'), datos.get('fecha'), datos.get('operario'),
            datos.get('cliente'), datos.get('obra'), datos.get('operarios'),
            datos.get('albaranes'), datos.get('material_stock'), datos.get('descripcion'),
            datos.get('terminado'), datos.get('tiempo_restante')
        ))
        conn.commit(); cur.close(); conn.close()
        return {'status': 'ok'}, 200
    except Exception as e:
        return {'error': str(e)}, 500

@app.route('/admin/reset-conversacion', methods=['POST'])
def admin_reset_conv():
    try:
        datos = request.get_json()
        numero = datos.get('numero', '')
        conn = get_db(); cur = conn.cursor()
        cur.execute("DELETE FROM conversaciones_db WHERE numero=%s", (numero,))
        conn.commit(); cur.close(); conn.close()
        return {'status': 'ok', 'msg': f'Conversación {numero} borrada'}, 200
    except Exception as e:
        return {'error': str(e)}, 500

@app.route('/admin/truncate-partes', methods=['POST'])
def admin_truncate():
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("TRUNCATE TABLE partes RESTART IDENTITY")
        conn.commit(); cur.close(); conn.close()
        return {'status': 'ok', 'msg': 'Tabla partes vaciada'}, 200
    except Exception as e:
        return {'error': str(e)}, 500

@app.route('/migrate', methods=['GET'])
def migrate():
    try:
        conn = get_db()
        cur = conn.cursor()
        for col, tipo in [('material_stock','TEXT'), ('terminado','TEXT'), ('tiempo_restante','TEXT'), ('pdf_descargado','BOOLEAN DEFAULT FALSE'), ('pdf_descargado_at','TIMESTAMP')]:
            try:
                cur.execute(f"ALTER TABLE partes ADD COLUMN IF NOT EXISTS {col} {tipo}")
                conn.commit()
            except Exception:
                conn.rollback()
        cur.close(); conn.close()
        return {'status': 'migración OK'}, 200
    except Exception as e:
        return {'error': str(e)}, 500

@app.route('/health', methods=['GET'])
def health():
    return {
        'status': 'ok',
        'service': 'partes-instapalma',
        'zapia_notify_url': 'SET' if os.environ.get('ZAPIA_NOTIFY_URL') else 'NOT SET',
        'zapia_notify_token': 'SET' if os.environ.get('ZAPIA_NOTIFY_TOKEN') else 'NOT SET',
        'supervisor_wa': os.environ.get('SUPERVISOR_WA', 'default'),
        'gmail_user': os.environ.get('GMAIL_USER', 'NOT SET'),
        'gmail_password': 'SET' if os.environ.get('GMAIL_APP_PASSWORD') else 'NOT SET'
    }, 200

@app.route('/vehiculos')
def panel_vehiculos():
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT id, matricula, marca_modelo, mes, km_inicio, km_fin, operario, created_at FROM vehiculos ORDER BY created_at DESC")
        rows = cur.fetchall(); cur.close(); conn.close()
    except Exception as e:
        rows = []
    filas = ""
    for r in rows:
        filas += f"""<tr class="clickable" onclick="window.location='/vehiculos/{r[0]}'">
            <td>{r[0]}</td><td><b>{r[1]}</b></td><td>{r[2]}</td><td>{r[3]}</td>
            <td>{r[4]}</td><td>{r[5]}</td><td>{nombre_operario(r[6] or '')}</td>
            <td>{str(r[7])[:10]}</td>
            <td><a href='/vehiculos/{r[0]}/pdf' onclick='event.stopPropagation()'>📄 PDF</a></td>
        </tr>"""
    html = f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
    <title>Vehículos — Instapalma</title>
    <style>{CSS_BASE}</style></head><body>
    <header><div><h1>🚗 Partes de Vehículos</h1><p>Instapalma</p></div></header>
    <div class='wrap'><table>
    <thead><tr><th>#</th><th>Matrícula</th><th>Modelo</th><th>Mes</th>
    <th>Km inicio</th><th>Km fin</th><th>Operario</th><th>Fecha</th><th>PDF</th></tr></thead>
    <tbody>{''.join([filas]) if rows else "<tr><td colspan=9 class='empty'>Sin registros</td></tr>"}</tbody>
    </table></div>
    <div style='padding:0 30px'><a href='/partes' class='back'>← Ver Partes de Trabajo</a></div>
    </body></html>"""
    from flask import Response
    return Response(html, mimetype='text/html')

@app.route('/vehiculos/<int:v_id>/pdf')
def descargar_pdf_vehiculo(v_id):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT * FROM vehiculos WHERE id=%s", (v_id,))
        r = cur.fetchone(); cur.close(); conn.close()
    except Exception as e:
        return f"Error: {e}", 500
    if not r:
        return "Vehículo no encontrado", 404
    datos = {
        'matricula': r[1], 'marca_modelo': r[2], 'mes': r[3],
        'km_inicio': r[4], 'km_fin': r[5], 'proximo_aceite': r[6],
        'estado_neumaticos': r[7], 'conductores': r[8], 'mantenimientos': r[9],
        'observaciones': r[10], 'golpes': r[11],
        'fecha': str(r[13])[:10] if r[13] else ''
    }
    pdf_bytes = generar_pdf_vehiculo(datos)
    mat = (r[1] or 'VEHICULO').replace(' ','_').upper()
    mes = (r[3] or '').replace('/','_').replace(' ','_')
    nombre = f"{mes}-{mat}-VEHICULO.pdf"
    try:
        from datetime import datetime as dt
        conn2 = get_db(); cur2 = conn2.cursor()
        cur2.execute("UPDATE vehiculos SET pdf_descargado=TRUE, pdf_descargado_at=%s WHERE id=%s", (dt.utcnow(), v_id))
        conn2.commit(); cur2.close(); conn2.close()
    except Exception:
        pass
    from flask import Response
    return Response(pdf_bytes, mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{nombre}"'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
