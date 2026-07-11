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
                devolucion_almacen TEXT,
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
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vacaciones (
                id SERIAL PRIMARY KEY,
                operario VARCHAR(100),
                nombre_operario VARCHAR(100),
                fecha_inicio VARCHAR(20),
                fecha_fin VARCHAR(20),
                dias_solicitados INTEGER DEFAULT 0,
                fecha_solicitud VARCHAR(20),
                estado VARCHAR(20) DEFAULT 'pendiente',
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS saldo_vacaciones (
                id SERIAL PRIMARY KEY,
                operario VARCHAR(100) UNIQUE,
                nombre VARCHAR(100),
                dias_totales INTEGER DEFAULT 23,
                dias_usados INTEGER DEFAULT 0,
                anio INTEGER DEFAULT 2026,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS resumen_mes (
                id SERIAL PRIMARY KEY,
                operario VARCHAR(100),
                nombre_operario VARCHAR(100),
                mes VARCHAR(30),
                horas_extra VARCHAR(20),
                dias_vacaciones VARCHAR(20),
                total_gastos VARCHAR(30),
                foto_url TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("DB inicializada OK")
    except Exception as e:
        print(f"Error init DB: {e}")

def guardar_parte(datos, numero_operario):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO partes (numero_parte, fecha, operario, cliente, obra, operarios, albaranes, material_stock, devolucion_almacen, descripcion, terminado, tiempo_restante)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            datos.get('devolucion_almacen', 'Ninguno'),
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
TWILIO_WA_NUMBER   = os.environ.get('TWILIO_WA_NUMBER', 'whatsapp:+19784402048')
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
        import json as _json
        # Serializar listas/dicts a JSON para almacenamiento correcto en jsonb
        if isinstance(valor, (list, dict)):
            valor_json = _json.dumps(valor, ensure_ascii=False)
        else:
            valor_json = valor
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            UPDATE conversaciones_db
            SET datos = datos || jsonb_build_object(%s, %s::text::jsonb), updated_at=NOW()
            WHERE numero=%s
        """, (clave, valor_json, numero))
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

    # Devolución a almacén
    elements.append(Paragraph("DEVOLUCION A ALMACEN", sec_style))
    dev = datos.get('devolucion_almacen', 'Ninguno')
    if normalizar(dev) == 'ninguno' or not dev:
        dev_rows = [['Material', 'Cantidad'], ['—', '—']]
    else:
        dev_rows = [['Material', 'Cantidad']]
        for linea in dev.split('\n'):
            linea = linea.strip()
            if not linea:
                continue
            if '—' in linea:
                parts = linea.split('—', 1)
            elif '-' in linea:
                parts = linea.split('-', 1)
            else:
                parts = [linea, '']
            dev_rows.append([parts[0].strip(), parts[1].strip() if len(parts) > 1 else ''])
    t_dev = Table(dev_rows, colWidths=[10*cm, 7*cm])
    t_dev.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#e65100')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
        ('PADDING', (0,0), (-1,-1), 6),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, GRIS]),
    ]))
    elements.append(t_dev)
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
        from twilio.rest import Client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        # Asegurar formato whatsapp:+XXXXX
        if not destino.startswith('whatsapp:'):
            destino = 'whatsapp:+' + destino.lstrip('+')
        kwargs = dict(from_=TWILIO_WA_NUMBER, to=destino, body=mensaje)
        if media_url:
            kwargs['media_url'] = [media_url]
        client.messages.create(**kwargs)
        print(f"WA enviado OK a {destino}")
    except Exception as e:
        print(f"Error WA: {e}")

def generar_resumen(datos):
    ops  = datos.get('operarios', 'Ninguno')
    albs = datos.get('albaranes', 'Ninguno')
    mat  = datos.get('material_stock', 'Ninguno')
    dev  = datos.get('devolucion_almacen', 'Ninguno')
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
        f"📦 Devolución almacén: {dev}\n"
        f"📝 Descripción: {desc}\n"
        f"🏁 Terminado: {linea_term}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"¿Es correcto? Responde *SÍ* para enviar o *NO* para cancelar."
    )

def finalizar_parte(numero, datos):
    ops  = datos.get('operarios', 'Ninguno')
    albs = datos.get('albaranes', 'Ninguno')
    mat  = datos.get('material_stock', 'Ninguno')
    dev  = datos.get('devolucion_almacen', 'Ninguno')
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
        f"📦 Devolución almacén: {dev}\n"
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


MENSAJES_VEHICULO   = ['vehiculo', 'vehículo', 'coche', 'camion', 'camión', 'furgoneta', 'mantenimiento vehiculo']
MENSAJES_VACACIONES = ['vacaciones', 'vacacion', 'solicitar vacaciones', 'pedir vacaciones', 'dias libres', 'días libres']
MENSAJES_RESUMEN_MES = ['resumen mes', 'resumen del mes', 'resumen mensual', 'cierre mes', 'cierre del mes', 'resumen fin de mes', 'fin de mes']
MENSAJES_STOCK_SALIDA   = ['salida']
MENSAJES_STOCK_DEVOL    = ['devolucion', 'devolución']
MENSAJES_STOCK_CONSULTA = ['consulta']

def iniciar_vacaciones(numero):
    try:
        conn = get_db(); cur = conn.cursor()
        datos = json.dumps({
            'tipo': 'vacaciones',
            'operario': numero,
            'fecha_inicio': '', 'fecha_fin': '',
            'fecha_solicitud': datetime.now().strftime('%d/%m/%Y')
        })
        cur.execute("""
            INSERT INTO conversaciones_db (numero, paso, datos, updated_at)
            VALUES (%s, 'vac_inicio', %s::jsonb, NOW())
            ON CONFLICT (numero) DO UPDATE SET paso='vac_inicio', datos=%s::jsonb, updated_at=NOW()
        """, (numero, datos, datos))
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        print(f"Error iniciar_vacaciones: {e}")

def calcular_dias_laborables(fecha_inicio, fecha_fin):
    """Calcula días laborables (lun-vie) entre dos fechas en formato DD/MM/YYYY."""
    try:
        from datetime import datetime as dt, timedelta
        fi = dt.strptime(fecha_inicio, '%d/%m/%Y')
        ff = dt.strptime(fecha_fin, '%d/%m/%Y')
        dias = 0
        current = fi
        while current <= ff:
            if current.weekday() < 5:  # lun-vie
                dias += 1
            current += timedelta(days=1)
        return dias
    except:
        return 0

def get_saldo_vacaciones(numero_operario):
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT dias_totales, dias_usados, nombre FROM saldo_vacaciones WHERE operario=%s", (numero_operario,))
        row = cur.fetchone()
        return row  # (dias_totales, dias_usados, nombre) o None
    except:
        return None
    finally:
        try:
            if conn: conn.close()
        except: pass

def guardar_vacacion(datos, numero_operario):
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        dias = calcular_dias_laborables(datos.get('fecha_inicio',''), datos.get('fecha_fin',''))
        nombre = datos.get('nombre_operario', nombre_operario(numero_operario))
        cur.execute("""
            INSERT INTO vacaciones (operario, nombre_operario, fecha_inicio, fecha_fin, dias_solicitados, fecha_solicitud, estado)
            VALUES (%s, %s, %s, %s, %s, %s, 'pendiente')
            RETURNING id
        """, (
            numero_operario,
            nombre,
            datos.get('fecha_inicio',''),
            datos.get('fecha_fin',''),
            dias,
            datos.get('fecha_solicitud','')
        ))
        vid = cur.fetchone()[0]
        conn.commit()
        return vid, dias
    except Exception as e:
        print(f"Error guardar_vacacion: {e}")
        if conn: conn.rollback()
        return None, 0
    finally:
        try:
            if conn: conn.close()
        except: pass

def aprobar_rechazar_vacacion(vac_id, estado):
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE vacaciones SET estado=%s WHERE id=%s RETURNING operario, nombre_operario, fecha_inicio, fecha_fin, dias_solicitados", (estado, vac_id))
        row = cur.fetchone()
        if row and estado == 'aprobada':
            # Descontar días del saldo
            operario, nombre, fi, ff, dias = row
            cur.execute("""
                UPDATE saldo_vacaciones SET dias_usados = dias_usados + %s, updated_at=NOW()
                WHERE operario=%s
            """, (dias or 0, operario))
        conn.commit()
        return row  # (operario, nombre, fecha_inicio, fecha_fin, dias)
    except Exception as e:
        print(f"Error aprobar_rechazar: {e}")
        if conn: conn.rollback()
        return None
    finally:
        try:
            if conn: conn.close()
        except: pass


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
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
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
        conn.commit()
        return vid
    except Exception as e:
        print(f"Error guardar_vehiculo: {e}")
        if conn:
            conn.rollback()
        return None
    finally:
        try:
            if conn:
                conn.close()
        except:
            pass

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
    import threading
    mat = datos.get('matricula','').replace(' ','_').upper()
    mes = datos.get('mes','').replace('/','_').replace(' ','_')
    nombre_pdf = f"{mes}-{mat}-VEHICULO.pdf"
    pdf_bytes = generar_pdf_vehiculo(datos)

    # Guardar en DB y enviar WhatsApp primero (no bloquear)
    vid = guardar_vehiculo(datos, numero)
    BOT_URL = os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'bot-production-66b8.up.railway.app')
    if vid:
        pdf_url = f"https://{BOT_URL}/vehiculos/{vid}/pdf"
        caption = f"Parte Vehiculo - {mat} - {mes}\nKm: {datos.get('km_inicio','')} a {datos.get('km_fin','')}"
        enviar_whatsapp(SUPERVISOR_WA, caption, media_url=pdf_url)
        op_wa = numero if numero.startswith("whatsapp:") else f"whatsapp:{numero}"
        enviar_whatsapp(op_wa, "Parte de vehiculo enviado. Aqui tienes tu copia:", media_url=pdf_url)

    borrar_estado(numero)

    # Email en hilo separado para no bloquear la respuesta
    def enviar_email_vehiculo():
        try:
            msg_email = MIMEMultipart()
            msg_email['From']    = GMAIL_USER
            msg_email['To']      = ', '.join([SUPERVISOR_EMAIL_1, SUPERVISOR_EMAIL_2])
            msg_email['Subject'] = f"[VEHICULO] Parte - {mat} - {mes}"
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

    threading.Thread(target=enviar_email_vehiculo, daemon=True).start()


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
            media_url = ''
        except (KeyError, IndexError):
            return 'OK', 200
    else:
        incoming_msg = request.form.get('Body', '').strip()
        numero = request.form.get('From', '')
        # Capturar media adjunta (foto enviada por el operario)
        num_media = int(request.form.get('NumMedia', 0))
        media_url = request.form.get('MediaUrl0', '') if num_media > 0 else ''

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
        iniciar_vehiculo(numero)
        msg.body(
            "🚗 *Bot de Vehículos — Instapalma*\n\n"
            "Vamos a registrar el parte mensual paso a paso.\n\n"
            "1️⃣ ¿Cuál es la *matrícula* del vehículo?"
        )
        return str(resp) if not use_meta else ('OK', 200)

    # ── Almacén: Salida ───────────────────────────────────────────────────────
    if normalizar(incoming_msg).strip() in MENSAJES_STOCK_SALIDA:
        num_limpio = numero.replace('whatsapp:','').replace('+','').strip()
        nombre_conocido = OPERARIOS.get(num_limpio, '')
        if nombre_conocido:
            set_dato(numero, 'nombre_operario', nombre_conocido)
        set_dato(numero, 'stock_lineas', [])
        set_paso(numero, 'stock_salida_obra')
        msg.body(
            "📤 *SALIDA DE ALMACÉN*\n\n"
            "¿Para qué *obra o cliente* es esta salida?\n"
            "_Ejemplo: Ayuntamiento Los Llanos — Calle Real_"
        )
        return str(resp) if not use_meta else ('OK', 200)

    # ── Almacén: Devolución ───────────────────────────────────────────────────
    if normalizar(incoming_msg).strip() in MENSAJES_STOCK_DEVOL:
        num_limpio = numero.replace('whatsapp:','').replace('+','').strip()
        nombre_conocido = OPERARIOS.get(num_limpio, '')
        if nombre_conocido:
            set_dato(numero, 'nombre_operario', nombre_conocido)
        set_dato(numero, 'stock_lineas', [])
        set_paso(numero, 'stock_devol_material')
        msg.body(
            "📥 *DEVOLUCIÓN A ALMACÉN*\n\n"
            "¿Qué material devuelves?\n"
            "_Escribe el nombre del material_"
        )
        return str(resp) if not use_meta else ('OK', 200)

    # ── Almacén: Consulta de stock ────────────────────────────────────────────
    if normalizar(incoming_msg).strip() in MENSAJES_STOCK_CONSULTA:
        # Intentar extraer el material de la misma frase
        msg_norm = normalizar(incoming_msg)
        busqueda = msg_norm
        for p in MENSAJES_STOCK_CONSULTA:
            busqueda = busqueda.replace(p, '').strip()
        if busqueda:
            mat, err = buscar_material_msg(busqueda)
            if err:
                msg.body(err)
            else:
                stock = mat[3]; minimo = mat[4]; unidad = mat[2]; nombre_mat = mat[1]
                alerta = "\n⚠️ *Stock por debajo del mínimo*" if stock <= minimo and minimo > 0 else ""
                msg.body(f"🔍 *{nombre_mat}*\nStock actual: *{stock} {unidad}*\nStock mínimo: {minimo} {unidad}{alerta}")
        else:
            set_paso(numero, 'stock_consulta')
            msg.body("🔍 ¿Qué material quieres consultar?\n_Escribe el nombre o parte de él_")
        return str(resp) if not use_meta else ('OK', 200)

    # Detectar resumen fin de mes
    if any(p in normalizar(incoming_msg) for p in MENSAJES_RESUMEN_MES):
        num_limpio = numero.replace('whatsapp:','').replace('+','').strip()
        nombre_conocido = OPERARIOS.get(num_limpio, '')
        if nombre_conocido:
            set_dato(numero, 'nombre_operario', nombre_conocido)
            set_paso(numero, 'resumen_mes')
        else:
            set_paso(numero, 'resumen_nombre')
        msg.body(
            "📊 *RESUMEN FIN DE MES — Instapalma*\n\n"
            + (f"Hola {nombre_conocido}! 👷\n\n" if nombre_conocido else "")
            + "1️⃣ ¿De qué *mes* es el resumen?\n_Ejemplo: Junio 2026_"
            if nombre_conocido else
            "📊 *RESUMEN FIN DE MES — Instapalma*\n\n"
            "Para empezar, ¿cuál es tu *nombre completo*?"
        )
        return str(resp) if not use_meta else ('OK', 200)

    # Detectar solicitud de vacaciones
    if any(p in normalizar(incoming_msg) for p in MENSAJES_VACACIONES):
        iniciar_vacaciones(numero)
        # Ver si el número está registrado
        num_limpio = numero.replace('whatsapp:','').replace('+','').strip()
        nombre_conocido = OPERARIOS.get(num_limpio, '')
        if nombre_conocido:
            set_dato(numero, 'nombre_operario', nombre_conocido)
            set_paso(numero, 'vac_inicio')
            saldo = get_saldo_vacaciones(numero.replace('whatsapp:','').replace('+','').strip())
            saldo_txt = f"\n📊 Días disponibles: *{saldo[0] - saldo[1]}* de {saldo[0]}" if saldo else ""
            msg.body(
                f"🌴 *Solicitud de Vacaciones — Instapalma*\n"
                f"Hola {nombre_conocido}!{saldo_txt}\n\n"
                f"1️⃣ ¿Cuál es la *fecha de inicio*?\n_Ejemplo: 14/07/2026_"
            )
        else:
            set_paso(numero, 'vac_nombre')
            msg.body(
                "🌴 *Solicitud de Vacaciones — Instapalma*\n\n"
                "Para continuar, ¿cuál es tu *nombre completo*?"
            )
        return str(resp) if not use_meta else ('OK', 200)

    # Gestión de aprobación por Alberto (APROBAR/RECHAZAR ID)
    if numero in [SUPERVISOR_WA, 'whatsapp:+34690875940']:
        msg_norm = normalizar(incoming_msg)
        import re as _re
        m = _re.match(r'^(aprobar|rechazar)\s+(\d+)$', msg_norm)
        if m:
            accion = m.group(1)
            vac_id = int(m.group(2))
            estado_nuevo = 'aprobada' if accion == 'aprobar' else 'rechazada'
            row = aprobar_rechazar_vacacion(vac_id, estado_nuevo)
            if row:
                op_num, op_nombre_bd, fi, ff, dias = row
                op_nombre = op_nombre_bd or nombre_operario(op_num)
                op_wa = op_num if op_num.startswith('whatsapp:') else f"whatsapp:{op_num}"
                num_limpio = op_num.replace('whatsapp:','').replace('+','').strip()
                saldo = get_saldo_vacaciones(num_limpio)
                if estado_nuevo == 'aprobada':
                    saldo_txt = f"\n📊 Te quedan *{saldo[0] - saldo[1]}* días de vacaciones." if saldo else ""
                    enviar_whatsapp(op_wa, f"✅ *Vacaciones aprobadas*\nTus vacaciones del {fi} al {ff} ({dias} días) han sido aprobadas por Alberto. ¡Disfrútalas! 🌴{saldo_txt}")
                    msg.body(f"✅ Vacaciones de {op_nombre} ({fi} – {ff}, {dias} días) aprobadas.")
                else:
                    enviar_whatsapp(op_wa, f"❌ *Vacaciones no aprobadas*\nTu solicitud del {fi} al {ff} no ha sido aprobada. Contacta con Alberto.")
                    msg.body(f"❌ Vacaciones de {op_nombre} rechazadas. El operario ha sido notificado.")
            else:
                msg.body(f"No encontré la solicitud #{vac_id}. Verifica el número.")
            return str(resp) if not use_meta else ('OK', 200)

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
        set_paso(numero, 'devolucion_almacen')
        msg.body(
            "6️⃣ *Devolución a Almacén*\n\n"
            "¿Devuelves algún material al almacén?\n"
            "_Ejemplo: Cable 2.5mm² — 10m sobrantes_\n\n"
            "Si no hay, escribe: *ninguno*"
        )

    elif paso == 'devolucion_almacen':
        val = incoming_msg if normalizar(incoming_msg) != 'ninguno' else 'Ninguno'
        set_dato(numero, 'devolucion_almacen', val)
        set_paso(numero, 'descripcion')
        msg.body("7️⃣ *Descripción* de los trabajos realizados:")

    elif paso == 'descripcion':
        set_dato(numero, 'descripcion', incoming_msg)
        set_paso(numero, 'terminado')
        msg.body("8️⃣ ¿El trabajo está *terminado*?\n\nResponde *SÍ* o *NO*")

    elif paso == 'terminado':
        if normalizar(incoming_msg) in ['si', 'sí', 's', 'yes']:
            set_dato(numero, 'terminado', 'Sí')
            set_dato(numero, 'tiempo_restante', '')
            set_paso(numero, 'confirmar')
            msg.body(generar_resumen(get_estado(numero)['datos']))
        elif normalizar(incoming_msg) in ['no', 'n']:
            set_dato(numero, 'terminado', 'No')
            set_paso(numero, 'tiempo_restante')
            msg.body("9️⃣ ¿Cuánto tiempo queda para terminarlo?\n\n_Ejemplo: 2 días, media jornada, 3 horas..._")
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

    # ── Flujo Resumen Fin de Mes ──────────────────────────────────────────────
    elif paso == 'resumen_nombre':
        set_dato(numero, 'nombre_operario', incoming_msg)
        set_paso(numero, 'resumen_mes')
        msg.body(f"👋 Hola *{incoming_msg}*!\n\n1️⃣ ¿De qué *mes* es el resumen?\n_Ejemplo: Junio 2026_")

    elif paso == 'resumen_mes':
        set_dato(numero, 'mes', incoming_msg)
        set_paso(numero, 'resumen_horas')
        msg.body("2️⃣ ¿Cuántas *horas extra* has realizado este mes?\n_Ejemplo: 8_")

    elif paso == 'resumen_horas':
        set_dato(numero, 'horas_extra', incoming_msg)
        set_paso(numero, 'resumen_vacaciones')
        msg.body("3️⃣ ¿Cuántos *días de vacaciones* has disfrutado este mes?\n_Ejemplo: 5_")

    elif paso == 'resumen_vacaciones':
        set_dato(numero, 'dias_vacaciones', incoming_msg)
        set_paso(numero, 'resumen_gastos')
        msg.body("4️⃣ ¿Cuál es el *total de gastos* del mes? (en €)\n_Ejemplo: 127.50_")

    elif paso == 'resumen_gastos':
        set_dato(numero, 'total_gastos', incoming_msg)
        set_paso(numero, 'resumen_foto')
        msg.body(
            "5️⃣ Adjunta la *foto del justificante* 📎\n"
            "_Es obligatoria para enviar el resumen._"
        )

    elif paso == 'resumen_foto':
        # Foto obligatoria — si no hay imagen, pedir de nuevo
        foto_url = media_url if media_url else ''
        if not foto_url:
            msg.body(
                "⚠️ Necesito que adjuntes la *foto del justificante*.\n"
                "Por favor envía la imagen para continuar."
            )
        else:
            set_dato(numero, 'foto_url', foto_url)
            set_paso(numero, 'resumen_confirmar')
            datos_r = get_estado(numero)['datos']
            msg.body(
                f"📊 *RESUMEN FIN DE MES*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📅 Mes: {datos_r.get('mes','')}\n"
                f"👷 {datos_r.get('nombre_operario','')}\n"
                f"⏱ Horas extra: {datos_r.get('horas_extra','0')}\n"
                f"🌴 Días vacaciones: {datos_r.get('dias_vacaciones','0')}\n"
                f"💶 Total gastos: {datos_r.get('total_gastos','0')} €\n"
                f"📎 Foto adjunta ✅\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"¿Es correcto? Responde *SÍ* o *NO*"
            )

    elif paso == 'resumen_confirmar':
        if es_confirmacion(incoming_msg):
            datos_r = get_estado(numero)['datos']
            borrar_estado(numero)
            finalizar_resumen_mes(numero, datos_r)
            msg.body("✅ Resumen enviado. Te llegará el PDF en un momento.")
        elif es_cancelacion(incoming_msg):
            borrar_estado(numero)
            msg.body("❌ Cancelado. Escribe *resumen mes* para empezar de nuevo.")
        else:
            msg.body("Responde *SÍ* para confirmar y enviar, o *NO* para cancelar.")

    # ── Flujo Almacén: Salida ─────────────────────────────────────────────────
    elif paso == 'stock_salida_obra':
        set_dato(numero, 'stock_obra', incoming_msg)
        set_paso(numero, 'stock_salida_material')
        msg.body(
            "📦 ¿Qué *material* retiras?\n"
            "_Escribe el nombre del material_"
        )

    elif paso == 'stock_salida_material':
        if normalizar(incoming_msg) in ['listo', 'fin', 'terminar', 'acabar', 'ya', 'eso es todo']:
            # Pasar a confirmación
            datos_s = get_estado(numero)['datos']
            lineas = datos_s.get('stock_lineas', [])
            if not lineas:
                msg.body("No has añadido ningún material. Dime qué material retiras.")
            else:
                set_paso(numero, 'stock_salida_confirmar')
                resumen = '\n'.join([f"• {l['material']} — {l['cantidad']} {l['unidad']}" for l in lineas])
                msg.body(
                    f"📤 *RESUMEN SALIDA*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🏢 Obra: {datos_s.get('stock_obra','')}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"{resumen}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"¿Es correcto? Responde *SÍ* o *NO*"
                )
        else:
            mat, err = buscar_material_msg(incoming_msg)
            if err:
                if isinstance(err, tuple) and err[0] == 'RETALES':
                    # Es un grupo de retales — preguntar metros necesarios
                    candidatos = err[1]
                    # Convertir Decimals a float para serialización JSON
                    candidatos_serial = [[c[0], c[1], c[2], float(c[3]), float(c[4])] for c in candidatos]
                    set_dato(numero, 'stock_retales_candidatos', candidatos_serial)
                    set_dato(numero, 'stock_mat_busqueda', incoming_msg)
                    set_paso(numero, 'stock_salida_retales_metros')
                    # Calcular total real extrayendo metros del nombre
                    sugerencia_previa = sugerir_retales(candidatos, 9999)
                    retales_info = sugerencia_previa['retales']
                    total_m = sugerencia_previa['total_disponible']
                    lista_r = '\n'.join([f"  • {r['nombre']} — {r['metros']} m" for r in retales_info])
                    msg.body(
                        f"📏 *Retales disponibles de {incoming_msg}:*\n{lista_r}\n"
                        f"Total: *{total_m} m*\n\n"
                        f"¿Cuántos metros necesitas?"
                    )
                else:
                    set_paso(numero, 'stock_salida_material')
                    msg.body(err)
            else:
                set_dato(numero, 'stock_mat_tmp', {'id': mat[0], 'nombre': mat[1], 'unidad': mat[2], 'stock': float(mat[3]), 'precio': float(mat[5]) if len(mat) > 5 and mat[5] else 0})
                set_paso(numero, 'stock_salida_cantidad')
                msg.body(
                    f"📦 *{mat[1]}*\n"
                    f"Stock disponible: *{mat[3]} {mat[2]}*\n\n"
                    f"¿Qué *cantidad* retiras?"
                )

    elif paso == 'stock_salida_retales_metros':
        try:
            metros_necesarios = float(incoming_msg.replace(',','.'))
            if metros_necesarios <= 0:
                msg.body("Los metros deben ser mayor que 0.")
            else:
                datos_s = get_estado(numero)['datos']
                candidatos = [tuple(c) for c in datos_s.get('stock_retales_candidatos', [])]
                sugerencia = sugerir_retales(candidatos, metros_necesarios)
                set_dato(numero, 'stock_retales_sugerencia', sugerencia)
                set_dato(numero, 'stock_retales_metros', metros_necesarios)

                if sugerencia['total_disponible'] < metros_necesarios:
                    msg.body(
                        f"⚠️ Solo hay *{sugerencia['total_disponible']} m* disponibles en total "
                        f"y necesitas *{metros_necesarios} m*. Stock insuficiente.\n\n"
                        f"¿Otro material? Escribe el nombre o di *listo*."
                    )
                    set_paso(numero, 'stock_salida_material')
                else:
                    set_paso(numero, 'stock_salida_retales_elegir')
                    texto = f"📐 Necesitas *{metros_necesarios} m*. Retales disponibles:\n\n"
                    for i, r in enumerate(sugerencia['retales'], 1):
                        suficiente = ' ✅' if r['metros'] >= metros_necesarios else ''
                        texto += f"*{i}.* {r['nombre']} — {r['metros']} m{suficiente}\n"
                    texto += "\n"
                    if sugerencia['individuales']:
                        mejor = sugerencia['individuales'][0]
                        texto += f"💡 *Sugerencia:* Retal {sugerencia['retales'].index(mejor)+1} ({mejor['metros']} m) cubre solo.\n"
                    elif sugerencia['combinacion']:
                        nums = [sugerencia['retales'].index(r)+1 for r in sugerencia['combinacion']]
                        total = sum(r['metros'] for r in sugerencia['combinacion'])
                        texto += f"💡 *Sugerencia:* Retales {'+'.join(map(str,nums))} = {total} m (combinación óptima).\n"
                    texto += "\nResponde el *número* del retal que coges, o varios separados por coma (ej: *1,3*)."
                    msg.body(texto)
        except:
            msg.body("Escribe solo el número de metros. Ejemplo: *10* o *5.5*")

    elif paso == 'stock_salida_retales_elegir':
        datos_s = get_estado(numero)['datos']
        sugerencia = datos_s.get('stock_retales_sugerencia', {})
        retales_disp = sugerencia.get('retales', [])
        try:
            # Parsear selección: "1", "1,3", "2 y 3"
            import re as _re
            nums_str = _re.findall(r'\d+', incoming_msg)
            seleccionados_idx = [int(n)-1 for n in nums_str if 0 < int(n) <= len(retales_disp)]
            if not seleccionados_idx:
                msg.body(f"Responde el número del retal (1-{len(retales_disp)}) o varios separados por coma.")
            else:
                lineas = datos_s.get('stock_lineas', [])
                for idx in seleccionados_idx:
                    r = retales_disp[idx]
                    lineas.append({
                        'material': r['nombre'],
                        'cantidad': r['metros'],   # metros en el albarán (informativo)
                        'unidad': r['unidad'],
                        'material_id': r['id'],
                        'delta_stock': -1,         # un retal = 1 unidad de stock
                        'precio': r.get('precio', 0)
                    })
                set_dato(numero, 'stock_lineas', lineas)
                set_paso(numero, 'stock_salida_material')
                añadidos = ', '.join([retales_disp[i]['nombre'] for i in seleccionados_idx])
                msg.body(
                    f"✅ Añadido: *{añadidos}*\n\n"
                    f"¿Otro material? Escribe el nombre o di *listo* para terminar."
                )
        except:
            msg.body(f"Responde el número del retal. Ejemplo: *1* o *1,2*")

    elif paso == 'stock_salida_cantidad':
        try:
            cantidad = float(incoming_msg.replace(',','.'))
            datos_s = get_estado(numero)['datos']
            mat_tmp = datos_s.get('stock_mat_tmp', {})
            if cantidad <= 0:
                msg.body("La cantidad debe ser mayor que 0.")
            elif cantidad > mat_tmp.get('stock', 0):
                msg.body(f"⚠️ Solo hay *{mat_tmp.get('stock',0)} {mat_tmp.get('unidad','')}* disponibles. Indica una cantidad menor.")
            else:
                lineas = datos_s.get('stock_lineas', [])
                lineas.append({'material': mat_tmp['nombre'], 'cantidad': cantidad, 'unidad': mat_tmp['unidad'], 'material_id': mat_tmp['id'], 'precio': mat_tmp.get('precio', 0)})
                set_dato(numero, 'stock_lineas', lineas)
                set_paso(numero, 'stock_salida_material')
                msg.body(
                    f"✅ Añadido: *{mat_tmp['nombre']}* — {cantidad} {mat_tmp['unidad']}\n\n"
                    f"¿Otro material? Escribe el nombre o di *listo* para terminar."
                )
        except:
            msg.body("Escribe solo el número de la cantidad. Ejemplo: *25* o *0.5*")

    elif paso == 'stock_salida_confirmar':
        if es_confirmacion(incoming_msg):
            datos_s = get_estado(numero)['datos']
            lineas = datos_s.get('stock_lineas', [])
            obra = datos_s.get('stock_obra', '')
            nombre_op = datos_s.get('nombre_operario', nombre_operario(numero))
            borrar_estado(numero)
            import threading as _th
            def _procesar_salida():
                numero_alb = siguiente_numero_albaran()
                from datetime import datetime as dt
                fecha_str = dt.now().strftime('%d/%m/%Y %H:%M')
                # Crear albarán en DB
                aid = crear_albaran(numero_alb, numero, nombre_op, obra, lineas)
                # Ajustar stock y registrar movimientos
                alertas = []
                for l in lineas:
                    # Si es un retal, el delta de stock es -1 (sacar el retal entero); si no, -cantidad
                    delta = l.get('delta_stock', -l['cantidad'])
                    r = ajustar_stock(l['material_id'], delta)
                    registrar_movimiento('salida', l['material_id'], l['material'], l['cantidad'],
                        l['unidad'], numero, nombre_op, obra, aid)
                    if r and r[0] <= r[1] and r[1] > 0:
                        alertas.append(f"⚠️ {r[2]}: quedan {r[0]} {r[3]} (mínimo {r[1]})")
                # Generar PDF
                pdf_bytes = generar_pdf_albaran({
                    'numero': numero_alb, 'nombre_operario': nombre_op,
                    'obra': obra, 'lineas': lineas, 'fecha': fecha_str
                })
                pdf_url = subir_pdf_albaran(pdf_bytes, numero_alb)
                # Enviar al operario
                op_wa = numero if numero.startswith('whatsapp:') else f'whatsapp:+{numero.lstrip("+")}'
                resumen_txt = '\n'.join([f"• {l['material']} — {l['cantidad']} {l['unidad']}" for l in lineas])
                texto = (
                    f"✅ *Albarán {numero_alb}*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🏢 {obra}\n{resumen_txt}\n"
                    f"━━━━━━━━━━━━━━━━━━━━"
                )
                enviar_whatsapp(op_wa, texto, media_url=pdf_url if pdf_url else None)
                # Enviar al supervisor
                enviar_whatsapp(SUPERVISOR_WA, f"📤 *Salida almacén — {numero_alb}*\n👷 {nombre_op}\n🏢 {obra}\n{resumen_txt}", media_url=pdf_url if pdf_url else None)
                # Alertas de stock bajo
                for a in alertas:
                    enviar_whatsapp(SUPERVISOR_WA, a)
            _th.Thread(target=_procesar_salida, daemon=True).start()
            msg.body(f"✅ Salida registrada. Te envío el albarán en un momento.")
        elif es_cancelacion(incoming_msg):
            borrar_estado(numero)
            msg.body("❌ Cancelado.")
        else:
            msg.body("Responde *SÍ* para confirmar o *NO* para cancelar.")

    # ── Flujo Almacén: Devolución ─────────────────────────────────────────────
    elif paso == 'stock_devol_material':
        if normalizar(incoming_msg) in ['listo', 'fin', 'terminar', 'acabar', 'ya', 'eso es todo']:
            datos_d = get_estado(numero)['datos']
            lineas = datos_d.get('stock_lineas', [])
            if not lineas:
                msg.body("No has añadido ningún material. Dime qué devuelves.")
            else:
                set_paso(numero, 'stock_devol_confirmar')
                resumen = '\n'.join([f"• {l['material']} — {l['cantidad']} {l['unidad']}" for l in lineas])
                msg.body(
                    f"📥 *RESUMEN DEVOLUCIÓN*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"{resumen}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"¿Es correcto? Responde *SÍ* o *NO*"
                )
        else:
            mat, err = buscar_material_msg(incoming_msg)
            if err:
                if isinstance(err, tuple) and err[0] == 'RETALES':
                    # Devolución de retal — mostrar lista y que elija cuál devuelve
                    candidatos = err[1]
                    candidatos_serial = [[c[0], c[1], c[2], float(c[3]), float(c[4])] for c in candidatos]
                    set_dato(numero, 'stock_retales_candidatos', candidatos_serial)
                    set_paso(numero, 'stock_devol_retales_elegir')
                    lista_r = '\n'.join([f"*{i}.* {c[1]} — {c[3]} {c[2]}" for i, c in enumerate(candidatos, 1)])
                    msg.body(
                        f"📥 *Retales de {incoming_msg}:*\n{lista_r}\n\n"
                        f"¿Qué retal devuelves? Responde el *número*."
                    )
                else:
                    msg.body(err)
            else:
                set_dato(numero, 'stock_mat_tmp', {'id': mat[0], 'nombre': mat[1], 'unidad': mat[2]})
                set_paso(numero, 'stock_devol_cantidad')
                msg.body(f"📥 *{mat[1]}*\n¿Qué *cantidad* devuelves?")

    elif paso == 'stock_devol_retales_elegir':
        datos_d = get_estado(numero)['datos']
        candidatos = [tuple(c) for c in datos_d.get('stock_retales_candidatos', [])]
        try:
            import re as _re
            nums_str = _re.findall(r'\d+', incoming_msg)
            seleccionados_idx = [int(n)-1 for n in nums_str if 0 < int(n) <= len(candidatos)]
            if not seleccionados_idx:
                msg.body(f"Responde el número del retal (1-{len(candidatos)}).")
            else:
                lineas = datos_d.get('stock_lineas', [])
                for idx in seleccionados_idx:
                    c = candidatos[idx]
                    lineas.append({'material': c[1], 'cantidad': float(c[3]), 'unidad': c[2], 'material_id': c[0]})
                set_dato(numero, 'stock_lineas', lineas)
                set_paso(numero, 'stock_devol_material')
                añadidos = ', '.join([candidatos[i][1] for i in seleccionados_idx])
                msg.body(
                    f"✅ Añadido: *{añadidos}*\n\n"
                    f"¿Otro material? Escribe el nombre o di *listo* para terminar."
                )
        except:
            msg.body(f"Responde el número del retal. Ejemplo: *1* o *1,2*")

    elif paso == 'stock_devol_cantidad':
        try:
            cantidad = float(incoming_msg.replace(',','.'))
            if cantidad <= 0:
                msg.body("La cantidad debe ser mayor que 0.")
            else:
                datos_d = get_estado(numero)['datos']
                mat_tmp = datos_d.get('stock_mat_tmp', {})
                lineas = datos_d.get('stock_lineas', [])
                lineas.append({'material': mat_tmp['nombre'], 'cantidad': cantidad, 'unidad': mat_tmp['unidad'], 'material_id': mat_tmp['id']})
                set_dato(numero, 'stock_lineas', lineas)
                set_paso(numero, 'stock_devol_material')
                msg.body(
                    f"✅ Añadido: *{mat_tmp['nombre']}* — {cantidad} {mat_tmp['unidad']}\n\n"
                    f"¿Otro material? Escribe el nombre o di *listo* para terminar."
                )
        except:
            msg.body("Escribe solo el número. Ejemplo: *10* o *2.5*")

    elif paso == 'stock_devol_confirmar':
        if es_confirmacion(incoming_msg):
            datos_d = get_estado(numero)['datos']
            lineas = datos_d.get('stock_lineas', [])
            nombre_op = datos_d.get('nombre_operario', nombre_operario(numero))
            borrar_estado(numero)
            import threading as _th
            def _procesar_devolucion():
                numero_alb = siguiente_numero_albaran()
                from datetime import datetime as dt
                fecha_str = dt.now().strftime('%d/%m/%Y %H:%M')
                aid = crear_albaran(numero_alb, numero, nombre_op, 'DEVOLUCIÓN A ALMACÉN', lineas)
                for l in lineas:
                    ajustar_stock(l['material_id'], l['cantidad'])
                    registrar_movimiento('devolucion', l['material_id'], l['material'], l['cantidad'],
                        l['unidad'], numero, nombre_op, '', aid)
                pdf_bytes = generar_pdf_albaran({
                    'numero': numero_alb, 'nombre_operario': nombre_op,
                    'obra': 'DEVOLUCIÓN A ALMACÉN', 'lineas': lineas, 'fecha': fecha_str, 'tipo': 'devolucion'
                })
                pdf_url = subir_pdf_albaran(pdf_bytes, numero_alb)
                op_wa = numero if numero.startswith('whatsapp:') else f'whatsapp:+{numero.lstrip("+")}'
                resumen_txt = '\n'.join([f"• {l['material']} — {l['cantidad']} {l['unidad']}" for l in lineas])
                texto = (
                    f"✅ *Albarán devolución {numero_alb}*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📥 Material devuelto al almacén\n{resumen_txt}\n"
                    f"━━━━━━━━━━━━━━━━━━━━"
                )
                enviar_whatsapp(op_wa, texto, media_url=pdf_url if pdf_url else None)
                enviar_whatsapp(SUPERVISOR_WA, f"📥 *Devolución almacén — {numero_alb}*\n👷 {nombre_op}\n{resumen_txt}",
                    media_url=pdf_url if pdf_url else None)
            _th.Thread(target=_procesar_devolucion, daemon=True).start()
            msg.body("✅ Devolución registrada. Stock actualizado. Te envío el albarán en un momento.")
        elif es_cancelacion(incoming_msg):
            borrar_estado(numero)
            msg.body("❌ Cancelado.")
        else:
            msg.body("Responde *SÍ* para confirmar o *NO* para cancelar.")

    # ── Flujo Almacén: Consulta ───────────────────────────────────────────────
    elif paso == 'stock_consulta':
        mat, err = buscar_material_msg(incoming_msg)
        borrar_estado(numero)
        if err:
            msg.body(err)
        else:
            stock = mat[3]; minimo = mat[4]; unidad = mat[2]; nombre_mat = mat[1]
            alerta = "\n⚠️ *Stock por debajo del mínimo*" if stock <= minimo and minimo > 0 else ""
            msg.body(f"🔍 *{nombre_mat}*\nStock actual: *{stock} {unidad}*\nStock mínimo: {minimo} {unidad}{alerta}")

    # ── Flujo vacaciones ──────────────────────────────────────────────────────
    elif paso == 'vac_nombre':
        set_dato(numero, 'nombre_operario', incoming_msg)
        set_paso(numero, 'vac_inicio')
        msg.body(
            f"👋 Hola *{incoming_msg}*\n\n"
            f"1️⃣ ¿Cuál es la *fecha de inicio* de tus vacaciones?\n_Ejemplo: 14/07/2026_"
        )

    elif paso == 'vac_inicio':
        set_dato(numero, 'fecha_inicio', incoming_msg)
        set_paso(numero, 'vac_fin')
        msg.body("2️⃣ ¿Cuál es la *fecha de fin*?\n_Ejemplo: 21/07/2026_")

    elif paso == 'vac_fin':
        set_dato(numero, 'fecha_fin', incoming_msg)
        set_paso(numero, 'vac_confirmar')
        datos_vac = get_estado(numero)['datos']
        fi = datos_vac.get('fecha_inicio','')
        ff = incoming_msg
        dias = calcular_dias_laborables(fi, ff)
        num_limpio = numero.replace('whatsapp:','').replace('+','').strip()
        saldo = get_saldo_vacaciones(num_limpio)
        saldo_txt = f"\n📊 Días disponibles tras solicitud: *{saldo[0] - saldo[1] - dias}* de {saldo[0]}" if saldo else ""
        op_nombre = datos_vac.get('nombre_operario', nombre_operario(numero))
        msg.body(
            f"🌴 *RESUMEN SOLICITUD DE VACACIONES*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👷 {op_nombre}\n"
            f"📅 Inicio: {fi}\n"
            f"📅 Fin: {ff}\n"
            f"📆 Días laborables: *{dias}*{saldo_txt}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"¿Confirmas la solicitud? Responde *SÍ* o *NO*"
        )

    elif paso == 'vac_confirmar':
        if es_confirmacion(incoming_msg):
            datos_vac = get_estado(numero)['datos']
            vac_id, dias = guardar_vacacion(datos_vac, numero)
            borrar_estado(numero)
            fi = datos_vac.get('fecha_inicio','')
            ff = datos_vac.get('fecha_fin','')
            op_nombre = datos_vac.get('nombre_operario', nombre_operario(numero))
            # Notificar a Alberto
            if vac_id:
                enviar_whatsapp(
                    SUPERVISOR_WA,
                    f"🌴 *SOLICITUD DE VACACIONES #{vac_id}*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"👷 {op_nombre}\n"
                    f"📅 Del {fi} al {ff}\n"
                    f"📆 {dias} días laborables\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"✅ *APROBAR {vac_id}*\n"
                    f"❌ *RECHAZAR {vac_id}*"
                )
                msg.body(f"✅ Solicitud #{vac_id} enviada. Pendiente de aprobación por Alberto.")
            else:
                msg.body("✅ Solicitud enviada. Pendiente de aprobación por Alberto.")
        elif es_cancelacion(incoming_msg):
            borrar_estado(numero)
            msg.body("❌ Solicitud cancelada. Escribe *vacaciones* para empezar de nuevo.")
        else:
            msg.body("Responde *SÍ* para confirmar o *NO* para cancelar.")

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
        cur.execute("SELECT id, numero_parte, fecha, operario, cliente, obra, operarios, albaranes, material_stock, devolucion_almacen, descripcion, terminado, tiempo_restante, created_at FROM partes WHERE id=%s", (parte_id,))
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
            cur.execute("SELECT id, numero_parte, fecha, operario, cliente, obra, operarios, albaranes, material_stock, devolucion_almacen, descripcion, terminado, tiempo_restante, created_at FROM partes ORDER BY created_at DESC LIMIT 200")
            rows = cur.fetchall()
            cur.close(); conn.close()
            partes = [{'id':r[0],'numero_parte':r[1],'fecha':r[2],'operario':r[3],'cliente':r[4],'obra':r[5],'operarios':r[6],'albaranes':r[7],'material_stock':r[8],'devolucion_almacen':r[9],'descripcion':r[10],'terminado':r[11],'tiempo_restante':r[12],'created_at':str(r[13])} for r in rows]
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

    terminado = r[11] or ''
    es_ok = 'í' in terminado.lower() or terminado.lower() == 'si'
    estado_html = f'<div class="estado-ok">✅ TRABAJO TERMINADO</div>' if es_ok \
        else f'<div class="estado-curso">🔄 EN CURSO — Tiempo restante: {r[12] or "no especificado"}</div>'
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
    <p>Fecha: {r[2] or '—'} &nbsp;·&nbsp; Registrado: {str(r[13])[:16] if r[13] else '—'}</p>
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
    <div class="campo"><label>Devolución a almacén</label><div class="val">{r[9] or '—'}</div></div>
    <div class="campo"><label>Descripción de trabajos</label><div class="val">{r[10] or '—'}</div></div>
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
        'devolucion_almacen': r[9] or 'Ninguno',
        'descripcion': r[10] or '', 'terminado': r[11] or '', 'tiempo_restante': r[12] or ''
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
            INSERT INTO partes (numero_parte, fecha, operario, cliente, obra, operarios, albaranes, material_stock, devolucion_almacen, descripcion, terminado, tiempo_restante)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            datos.get('numero_parte'), datos.get('fecha'), datos.get('operario'),
            datos.get('cliente'), datos.get('obra'), datos.get('operarios'),
            datos.get('albaranes'), datos.get('material_stock'), datos.get('devolucion_almacen','Ninguno'),
            datos.get('descripcion'), datos.get('terminado'), datos.get('tiempo_restante')
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
        # Columnas tabla partes
        for col, tipo in [('material_stock','TEXT'), ('devolucion_almacen','TEXT'), ('terminado','TEXT'), ('tiempo_restante','TEXT'), ('pdf_descargado','BOOLEAN DEFAULT FALSE'), ('pdf_descargado_at','TIMESTAMP')]:
            try:
                cur.execute(f"ALTER TABLE partes ADD COLUMN IF NOT EXISTS {col} {tipo}")
                conn.commit()
            except Exception:
                conn.rollback()
        # Crear tabla vehiculos si no existe
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
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vacaciones (
                id SERIAL PRIMARY KEY,
                operario VARCHAR(100),
                nombre_operario VARCHAR(100),
                fecha_inicio VARCHAR(20),
                fecha_fin VARCHAR(20),
                dias_solicitados INTEGER DEFAULT 0,
                fecha_solicitud VARCHAR(20),
                estado VARCHAR(20) DEFAULT 'pendiente',
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        # Añadir columnas si la tabla ya existía sin ellas
        for col, tipo in [('nombre_operario','VARCHAR(100)'), ('dias_solicitados','INTEGER DEFAULT 0')]:
            try:
                cur.execute(f"ALTER TABLE vacaciones ADD COLUMN IF NOT EXISTS {col} {tipo}")
                conn.commit()
            except Exception:
                conn.rollback()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS saldo_vacaciones (
                id SERIAL PRIMARY KEY,
                operario VARCHAR(100) UNIQUE,
                nombre VARCHAR(100),
                dias_totales INTEGER DEFAULT 23,
                dias_usados INTEGER DEFAULT 0,
                anio INTEGER DEFAULT 2026,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS resumen_mes (
                id SERIAL PRIMARY KEY,
                operario VARCHAR(100),
                nombre_operario VARCHAR(100),
                mes VARCHAR(30),
                horas_extra VARCHAR(20),
                dias_vacaciones VARCHAR(20),
                total_gastos VARCHAR(30),
                foto_url TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
        return {'status': 'migración OK, tablas vehiculos, vacaciones, saldo_vacaciones y resumen_mes creadas'}, 200
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


# ── Panel Vacaciones ──────────────────────────────────────────────────────────
@app.route('/vacaciones')
def panel_vacaciones():
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT id, nombre_operario, operario, fecha_inicio, fecha_fin, dias_solicitados, fecha_solicitud, estado, created_at FROM vacaciones ORDER BY created_at DESC")
        rows = cur.fetchall()
        cur.execute("SELECT operario, nombre, dias_totales, dias_usados FROM saldo_vacaciones ORDER BY nombre")
        saldos = cur.fetchall()
        cur.close(); conn.close()
    except Exception as e:
        return f"Error: {e}", 500

    filas_vac = ''
    for r in rows:
        vid, nombre, op, fi, ff, dias, fsol, estado, cat = r
        badge = {'aprobada':'badge-ok','rechazada':'badge-no','pendiente':'badge-pend'}.get(estado,'badge-pend')
        filas_vac += f"<tr><td>{vid}</td><td>{nombre or op}</td><td>{fi}</td><td>{ff}</td><td>{dias or '-'}</td><td>{fsol}</td><td><span class='{badge}'>{estado.upper()}</span></td></tr>"
    if not filas_vac:
        filas_vac = "<tr><td colspan=7 class='empty'>Sin solicitudes</td></tr>"

    filas_saldo = ''
    for s in saldos:
        op, nombre, tot, usados = s
        restantes = tot - usados
        color = 'color:#c62828' if restantes < 5 else 'color:#2e7d32'
        filas_saldo += f"<tr><td>{nombre}</td><td>{tot}</td><td>{usados}</td><td style='{color};font-weight:700'>{restantes}</td><td><a href='/vacaciones/saldo/{op}/editar' style='color:#1a3a5c;font-size:12px'>✏️ Editar</a></td></tr>"
    if not filas_saldo:
        filas_saldo = "<tr><td colspan=5 class='empty'>Sin saldos configurados</td></tr>"

    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'><title>Vacaciones — Instapalma</title>
    <style>{CSS_BASE}
    .badge-ok{{background:#2e7d32;color:white;padding:3px 10px;border-radius:10px;font-size:11px}}
    .badge-no{{background:#c62828;color:white;padding:3px 10px;border-radius:10px;font-size:11px}}
    .badge-pend{{background:#e65100;color:white;padding:3px 10px;border-radius:10px;font-size:11px}}
    </style></head><body>
    <header><div><h1>🌴 Vacaciones</h1><p>Instapalma</p></div>
    <a href='/vacaciones/saldo/nuevo' style='background:white;color:#1a3a5c;padding:8px 16px;border-radius:8px;font-weight:700;text-decoration:none;font-size:13px'>+ Añadir saldo</a>
    </header>
    <div class='wrap' style='padding-top:20px'>
    <h3 style='color:#1a3a5c;margin-bottom:12px'>Saldo de Vacaciones por Operario</h3>
    <table><thead><tr><th>Operario</th><th>Total días</th><th>Usados</th><th>Restantes</th><th></th></tr></thead>
    <tbody>{filas_saldo}</tbody></table>
    <h3 style='color:#1a3a5c;margin:24px 0 12px'>Solicitudes</h3>
    <table><thead><tr><th>#</th><th>Operario</th><th>Inicio</th><th>Fin</th><th>Días</th><th>Solicitado</th><th>Estado</th></tr></thead>
    <tbody>{filas_vac}</tbody></table>
    </div>
    <div style='padding:0 30px'><a href='/partes' class='back'>← Ver Partes de Trabajo</a></div>
    </body></html>"""

@app.route('/vacaciones/saldo/nuevo', methods=['GET','POST'])
@app.route('/vacaciones/saldo/<path:op>/editar', methods=['GET','POST'])
def editar_saldo(op=None):
    from flask import request as req
    if req.method == 'POST':
        nombre = req.form.get('nombre','')
        operario = req.form.get('operario','')
        dias_totales = int(req.form.get('dias_totales', 23))
        dias_usados = int(req.form.get('dias_usados', 0))
        try:
            conn = get_db(); cur = conn.cursor()
            cur.execute("""
                INSERT INTO saldo_vacaciones (operario, nombre, dias_totales, dias_usados, anio, updated_at)
                VALUES (%s, %s, %s, %s, 2026, NOW())
                ON CONFLICT (operario) DO UPDATE SET nombre=%s, dias_totales=%s, dias_usados=%s, updated_at=NOW()
            """, (operario, nombre, dias_totales, dias_usados, nombre, dias_totales, dias_usados))
            conn.commit(); cur.close(); conn.close()
        except Exception as e:
            return f"Error: {e}", 500
        from flask import redirect
        return redirect('/vacaciones')

    # GET — cargar datos si es edición
    datos = {'operario': op or '', 'nombre': '', 'dias_totales': 23, 'dias_usados': 0}
    if op:
        try:
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT operario, nombre, dias_totales, dias_usados FROM saldo_vacaciones WHERE operario=%s", (op,))
            r = cur.fetchone()
            cur.close(); conn.close()
            if r:
                datos = {'operario': r[0], 'nombre': r[1], 'dias_totales': r[2], 'dias_usados': r[3]}
        except: pass
    titulo = 'Editar saldo' if op else 'Nuevo saldo'
    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'><title>{titulo} — Instapalma</title>
    <style>{CSS_BASE} label{{display:block;margin-bottom:4px;font-size:13px;font-weight:600;color:#555}}
    input{{width:100%;padding:10px;border:1px solid #ddd;border-radius:8px;font-size:14px;margin-bottom:16px;box-sizing:border-box}}
    .btn{{background:#1a3a5c;color:white;padding:12px 28px;border:none;border-radius:8px;font-size:15px;font-weight:700;cursor:pointer}}</style></head><body>
    <header><div><h1>🌴 {titulo}</h1><p>Instapalma</p></div></header>
    <div style='max-width:500px;margin:30px auto;background:white;padding:28px;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,.1)'>
    <form method='POST'>
    <label>Número operario (ej: 34636606175)</label><input name='operario' value='{datos["operario"]}' required>
    <label>Nombre</label><input name='nombre' value='{datos["nombre"]}' required>
    <label>Días totales 2026</label><input name='dias_totales' type='number' value='{datos["dias_totales"]}' required>
    <label>Días ya usados</label><input name='dias_usados' type='number' value='{datos["dias_usados"]}' required>
    <button class='btn' type='submit'>Guardar</button>
    </form>
    </div>
    <div style='padding:0 30px'><a href='/vacaciones' class='back'>← Volver</a></div>
    </body></html>"""


# ── Resumen Fin de Mes ────────────────────────────────────────────────────────

def guardar_resumen_mes(datos, numero_operario):
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        nombre = datos.get('nombre_operario', nombre_operario(numero_operario))
        cur.execute("""
            INSERT INTO resumen_mes (operario, nombre_operario, mes, horas_extra, dias_vacaciones, total_gastos, foto_url, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            RETURNING id
        """, (
            numero_operario,
            nombre,
            datos.get('mes', ''),
            datos.get('horas_extra', '0'),
            datos.get('dias_vacaciones', '0'),
            datos.get('total_gastos', '0'),
            datos.get('foto_url', ''),
        ))
        rid = cur.fetchone()[0]
        conn.commit()
        return rid
    except Exception as e:
        print(f"Error guardar_resumen_mes: {e}")
        if conn: conn.rollback()
        return None
    finally:
        try:
            if conn: conn.close()
        except: pass


def generar_pdf_resumen_mes(datos):
    """Genera PDF del resumen de fin de mes y devuelve bytes."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    elements = []
    AZUL = colors.HexColor('#1a3a5c')
    GRIS = colors.HexColor('#f5f5f5')

    titulo_style = ParagraphStyle('titulo', fontSize=20, textColor=AZUL,
        alignment=TA_CENTER, spaceAfter=4, fontName='Helvetica-Bold')
    pie_style = ParagraphStyle('pie', fontSize=7, textColor=colors.grey, alignment=TA_CENTER)

    import os as _os
    LOGO_PATH = _os.path.join(_os.path.dirname(__file__), 'logo.jpg')
    if _os.path.exists(LOGO_PATH):
        _logo_ratio = 1024 / 219
        _logo_w = 4 * cm
        _logo_h = _logo_w / _logo_ratio
        logo_img = RLImage(LOGO_PATH, width=_logo_w, height=_logo_h)
    else:
        logo_img = Paragraph("INSTAPALMA", titulo_style)

    cab_title_style = ParagraphStyle('cab_title', fontName='Helvetica-Bold', fontSize=16,
        textColor=AZUL, alignment=1, leading=20)
    cab = Table([[logo_img, Paragraph('RESUMEN FIN DE MES', cab_title_style)]], colWidths=[5*cm, 12*cm])
    cab.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
    ]))
    elements.append(cab)
    elements.append(HRFlowable(width="100%", thickness=2, color=AZUL, spaceAfter=10))
    elements.append(Spacer(1, 0.3*cm))

    filas = [
        ['Mes',                   datos.get('mes','')],
        ['Operario',              datos.get('nombre_operario','')],
        ['Horas extra',           datos.get('horas_extra','0')],
        ['Días vacaciones',       datos.get('dias_vacaciones','0')],
        ['Total gastos',          f"{datos.get('total_gastos','0')} €"],
    ]
    t = Table(filas, colWidths=[5*cm, 12*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,-1), AZUL),
        ('TEXTCOLOR', (0,0), (0,-1), colors.white),
        ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 11),
        ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
        ('PADDING', (0,0), (-1,-1), 8),
        ('ROWBACKGROUNDS', (1,0), (1,-1), [colors.white, GRIS]),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 0.5*cm))

    # Foto si existe
    foto_url = datos.get('foto_url', '')
    if foto_url:
        try:
            import urllib.request as _ur
            import tempfile, os as _os
            suffix = '.jpg'
            if '.png' in foto_url.lower(): suffix = '.png'
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            _ur.urlretrieve(foto_url, tmp.name)
            sec_style = ParagraphStyle('sec', fontSize=10, textColor=colors.white,
                backColor=AZUL, fontName='Helvetica-Bold', spaceAfter=4, spaceBefore=6, borderPad=4)
            elements.append(Paragraph('JUSTIFICANTE / FOTO', sec_style))
            elements.append(Spacer(1, 0.2*cm))
            img_w = 14*cm
            from PIL import Image as PILImg
            with PILImg.open(tmp.name) as im:
                w, h = im.size
                img_h = img_w * h / w
                if img_h > 18*cm:
                    img_h = 18*cm
                    img_w = img_h * w / h
            foto_img = RLImage(tmp.name, width=img_w, height=img_h)
            elements.append(foto_img)
            _os.unlink(tmp.name)
        except Exception as e:
            print(f"Error foto PDF: {e}")

    elements.append(Spacer(1, 1*cm))
    elements.append(Paragraph("Instapalma · Resumen generado automáticamente", pie_style))

    doc.build(elements)
    return buffer.getvalue()


def subir_pdf_resumen_mes(pdf_bytes, rid):
    """Sube el PDF a Cloudinary y devuelve la URL."""
    try:
        import cloudinary, cloudinary.uploader
        cloudinary.config(
            cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME',''),
            api_key=os.environ.get('CLOUDINARY_API_KEY',''),
            api_secret=os.environ.get('CLOUDINARY_API_SECRET','')
        )
        import base64 as _b64
        b64 = _b64.b64encode(pdf_bytes).decode()
        result = cloudinary.uploader.upload(
            f"data:application/pdf;base64,{b64}",
            public_id=f"resumen_mes_{rid}",
            resource_type='raw',
            folder='instapalma_resumenes'
        )
        return result.get('secure_url','')
    except Exception as e:
        print(f"Error subir PDF resumen: {e}")
        return ''


def finalizar_resumen_mes(numero, datos):
    """Guarda, genera PDF y envía a supervisor y operario."""
    import threading
    def _enviar():
        rid = guardar_resumen_mes(datos, numero)
        nombre_op = datos.get('nombre_operario', nombre_operario(numero))
        mes = datos.get('mes','')
        horas = datos.get('horas_extra','0')
        dias_vac = datos.get('dias_vacaciones','0')
        gastos = datos.get('total_gastos','0')

        texto = (
            f"📊 *RESUMEN FIN DE MES #{rid}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 Mes: {mes}\n"
            f"👷 {nombre_op}\n"
            f"⏱ Horas extra: {horas}\n"
            f"🌴 Días vacaciones: {dias_vac}\n"
            f"💶 Total gastos: {gastos} €\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )

        # Generar y subir PDF
        pdf_bytes = generar_pdf_resumen_mes(datos)
        pdf_url = subir_pdf_resumen_mes(pdf_bytes, rid)

        # Enviar al supervisor
        enviar_whatsapp(SUPERVISOR_WA, texto, media_url=pdf_url if pdf_url else None)
        # Enviar al operario
        op_wa = numero if numero.startswith('whatsapp:') else f'whatsapp:+{numero.lstrip("+")}'
        enviar_whatsapp(op_wa, f"✅ Resumen de {mes} enviado correctamente. Aquí tienes tu copia:", media_url=pdf_url if pdf_url else None)

    t = threading.Thread(target=_enviar, daemon=True)
    t.start()


# ── Panel web Resumen Fin de Mes ──────────────────────────────────────────────
@app.route('/resumenes')
def panel_resumenes():
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            SELECT id, nombre_operario, operario, mes, horas_extra, dias_vacaciones, total_gastos, foto_url, created_at
            FROM resumen_mes ORDER BY created_at DESC
        """)
        rows = cur.fetchall()
        cur.close(); conn.close()
    except Exception as e:
        return f"Error: {e}", 500

    filas = ''
    for r in rows:
        rid, nombre, op, mes, horas, dias_vac, gastos, foto_url, cat = r
        foto_link = f"<a href='{foto_url}' target='_blank'>📎 Ver</a>" if foto_url else '-'
        fecha = str(cat)[:10] if cat else ''
        filas += (
            f"<tr>"
            f"<td>{rid}</td>"
            f"<td>{nombre or op}</td>"
            f"<td>{mes}</td>"
            f"<td>{horas}</td>"
            f"<td>{dias_vac}</td>"
            f"<td>{gastos} €</td>"
            f"<td>{foto_link}</td>"
            f"<td>{fecha}</td>"
            f"<td><a href='/resumenes/{rid}/pdf' style='color:#1a3a5c;font-size:12px'>⬇ PDF</a></td>"
            f"</tr>"
        )
    if not filas:
        filas = "<tr><td colspan=9 class='empty'>Sin resúmenes aún</td></tr>"

    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
    <title>Resumen Fin de Mes — Instapalma</title>
    <style>{CSS_BASE}</style></head><body>
    <header><div><h1>📊 Resumen Fin de Mes</h1><p>Instapalma</p></div></header>
    <div class='wrap' style='padding-top:20px'>
    <table><thead><tr>
    <th>#</th><th>Operario</th><th>Mes</th><th>H. Extra</th><th>Días Vac.</th><th>Gastos</th><th>Foto</th><th>Fecha</th><th>PDF</th>
    </tr></thead><tbody>{filas}</tbody></table>
    </div>
    <div style='padding:0 30px'><a href='/partes' class='back'>← Ver Partes de Trabajo</a></div>
    </body></html>"""


@app.route('/resumenes/<int:rid>/pdf')
def pdf_resumen(rid):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT nombre_operario, operario, mes, horas_extra, dias_vacaciones, total_gastos, foto_url FROM resumen_mes WHERE id=%s", (rid,))
        r = cur.fetchone(); cur.close(); conn.close()
    except Exception as e:
        return f"Error: {e}", 500
    if not r:
        return "No encontrado", 404
    datos = {
        'nombre_operario': r[0] or r[1],
        'mes': r[2], 'horas_extra': r[3],
        'dias_vacaciones': r[4], 'total_gastos': r[5],
        'foto_url': r[6] or ''
    }
    pdf_bytes = generar_pdf_resumen_mes(datos)
    nombre_fichero = f"RESUMEN-{(r[2] or 'MES').replace(' ','_').upper()}-{(r[0] or '').replace(' ','_').upper()}.pdf"
    from flask import Response
    return Response(pdf_bytes, mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{nombre_fichero}"'})


    return Response(pdf_bytes, mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{nombre_fichero}"'})


# ══════════════════════════════════════════════════════════════════════════════
# GESTIÓN DE STOCK DE ALMACÉN
# ══════════════════════════════════════════════════════════════════════════════

MENSAJES_STOCK_SALIDA    = ['salida']
MENSAJES_STOCK_DEVOL     = ['devolucion', 'devolución']
MENSAJES_STOCK_CONSULTA  = ['consulta']

# ── DB ────────────────────────────────────────────────────────────────────────

def init_stock_db():
    conn = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS stock_materiales (
                id SERIAL PRIMARY KEY,
                nombre VARCHAR(200) UNIQUE,
                unidad VARCHAR(30) DEFAULT 'ud',
                stock_actual NUMERIC(12,3) DEFAULT 0,
                stock_minimo NUMERIC(12,3) DEFAULT 0,
                precio_unitario NUMERIC(12,4) DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        # Migración: añadir columna precio_unitario si no existe
        cur.execute("""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='stock_materiales' AND column_name='precio_unitario'
                ) THEN
                    ALTER TABLE stock_materiales ADD COLUMN precio_unitario NUMERIC(12,4) DEFAULT 0;
                END IF;
            END $$;
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS stock_movimientos (
                id SERIAL PRIMARY KEY,
                tipo VARCHAR(20),
                material_id INTEGER REFERENCES stock_materiales(id),
                material_nombre VARCHAR(200),
                cantidad NUMERIC(12,3),
                unidad VARCHAR(30),
                operario VARCHAR(100),
                nombre_operario VARCHAR(100),
                obra VARCHAR(200),
                albaran_id INTEGER,
                notas TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS stock_albaranes (
                id SERIAL PRIMARY KEY,
                numero VARCHAR(30) UNIQUE,
                operario VARCHAR(100),
                nombre_operario VARCHAR(100),
                obra VARCHAR(200),
                lineas JSONB,
                pdf_url TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.commit(); cur.close(); conn.close()
        print("Stock DB OK")
    except Exception as e:
        print(f"Error init_stock_db: {e}")
        if conn:
            try: conn.rollback()
            except: pass
    finally:
        try:
            if conn: conn.close()
        except: pass

init_stock_db()

def get_material_by_nombre(nombre):
    """Busca material por nombre exacto o aproximado. Devuelve (id, nombre, unidad, stock_actual, stock_minimo)."""
    import unicodedata as _ud
    def _norm(t):
        # Quitar tildes y pasar a minúsculas para comparación tolerante
        return ''.join(c for c in _ud.normalize('NFD', t.lower()) if _ud.category(c) != 'Mn')

    conn = None
    try:
        conn = get_db(); cur = conn.cursor()
        # Exacto (sin tildes)
        cur.execute("SELECT id, nombre, unidad, stock_actual, stock_minimo, precio_unitario FROM stock_materiales WHERE LOWER(nombre)=LOWER(%s)", (nombre,))
        r = cur.fetchone()
        if r:
            return r
        # Aproximado con ILIKE sobre cada palabra del texto buscado (tolerante a tildes)
        palabras = [p for p in _norm(nombre).split() if len(p) > 1]
        # Buscar todos y filtrar en Python para tolerancia de tildes
        cur.execute("SELECT id, nombre, unidad, stock_actual, stock_minimo, precio_unitario FROM stock_materiales ORDER BY nombre")
        todos = cur.fetchall()
        cur.close(); conn.close()
        resultados = []
        for row in todos:
            nombre_norm = _norm(row[1])
            if all(p in nombre_norm for p in palabras):
                resultados.append(row)
        if resultados:
            return resultados[:5]
        # Fallback: cualquier palabra coincide (búsqueda laxa)
        resultados_laxa = []
        for row in todos:
            nombre_norm = _norm(row[1])
            if any(p in nombre_norm for p in palabras if len(p) > 2):
                resultados_laxa.append(row)
        return resultados_laxa[:5]
    except Exception as e:
        print(f"Error get_material: {e}")
        return None
    finally:
        try:
            if conn: conn.close()
        except: pass

def ajustar_stock(material_id, delta):
    """Suma delta al stock (delta negativo = salida)."""
    conn = None
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            UPDATE stock_materiales SET stock_actual = stock_actual + %s, updated_at=NOW()
            WHERE id=%s RETURNING stock_actual, stock_minimo, nombre, unidad
        """, (delta, material_id))
        r = cur.fetchone()
        conn.commit(); cur.close(); conn.close()
        return r  # (stock_actual, stock_minimo, nombre, unidad)
    except Exception as e:
        print(f"Error ajustar_stock: {e}")
        if conn: conn.rollback()
        return None
    finally:
        try:
            if conn: conn.close()
        except: pass

def registrar_movimiento(tipo, material_id, material_nombre, cantidad, unidad, operario, nombre_op, obra='', albaran_id=None, notas=''):
    conn = None
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            INSERT INTO stock_movimientos (tipo, material_id, material_nombre, cantidad, unidad, operario, nombre_operario, obra, albaran_id, notas)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (tipo, material_id, material_nombre, cantidad, unidad, operario, nombre_op, obra, albaran_id, notas))
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        print(f"Error registrar_movimiento: {e}")
        if conn: conn.rollback()
    finally:
        try:
            if conn: conn.close()
        except: pass

def crear_albaran(numero, operario, nombre_op, obra, lineas):
    """Crea albarán y devuelve su id."""
    conn = None
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            INSERT INTO stock_albaranes (numero, operario, nombre_operario, obra, lineas)
            VALUES (%s,%s,%s,%s,%s) RETURNING id
        """, (numero, operario, nombre_op, obra, json.dumps(lineas)))
        aid = cur.fetchone()[0]
        conn.commit(); cur.close(); conn.close()
        return aid
    except Exception as e:
        print(f"Error crear_albaran: {e}")
        if conn: conn.rollback()
        return None
    finally:
        try:
            if conn: conn.close()
        except: pass

def siguiente_numero_albaran():
    conn = None
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM stock_albaranes")
        n = cur.fetchone()[0] + 1
        cur.close(); conn.close()
        from datetime import datetime as dt
        return f"ALB-{dt.now().strftime('%Y%m')}-{n:04d}"
    except:
        from datetime import datetime as dt
        return f"ALB-{dt.now().strftime('%Y%m%d%H%M%S')}"
    finally:
        try:
            if conn: conn.close()
        except: pass

# ── PDF albarán interno ───────────────────────────────────────────────────────

def generar_pdf_albaran(albaran):
    """albaran: dict con numero, nombre_operario, obra, lineas, fecha, tipo ('salida'|'devolucion')."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    elements = []
    AZUL = colors.HexColor('#1a3a5c')
    GRIS = colors.HexColor('#f5f5f5')

    titulo_style = ParagraphStyle('t', fontSize=20, textColor=AZUL,
        alignment=TA_CENTER, fontName='Helvetica-Bold')
    pie_style = ParagraphStyle('p', fontSize=7, textColor=colors.grey, alignment=TA_CENTER)

    import os as _os
    LOGO_PATH = _os.path.join(_os.path.dirname(__file__), 'logo.jpg')
    if _os.path.exists(LOGO_PATH):
        _lw = 4*cm; _lh = _lw / (1024/219)
        logo = RLImage(LOGO_PATH, width=_lw, height=_lh)
    else:
        logo = Paragraph("INSTAPALMA", titulo_style)

    es_devol = albaran.get('tipo','') == 'devolucion' or 'DEVOLUCI' in albaran.get('obra','').upper()
    titulo_alb = '📥 ALBARÁN DEVOLUCIÓN A ALMACÉN' if es_devol else '📤 ALBARÁN INTERNO DE ALMACÉN'
    titulo_sec = 'MATERIALES DEVUELTOS' if es_devol else 'MATERIALES RETIRADOS'

    cab_style = ParagraphStyle('c', fontName='Helvetica-Bold', fontSize=16,
        textColor=AZUL, alignment=1, leading=20)
    cab = Table([[logo, Paragraph(titulo_alb, cab_style)]], colWidths=[5*cm, 12*cm])
    cab.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0)]))
    elements.append(cab)
    elements.append(HRFlowable(width="100%", thickness=2, color=AZUL, spaceAfter=8))
    elements.append(Spacer(1, 0.2*cm))

    from datetime import datetime as dt
    fecha_str = albaran.get('fecha', dt.now().strftime('%d/%m/%Y %H:%M'))
    obra_label = 'Devuelto por' if es_devol else 'Obra / Cliente'
    t_cab = Table([
        ['Nº Albarán', albaran.get('numero','')],
        ['Fecha', fecha_str],
        ['Operario', albaran.get('nombre_operario','')],
        [obra_label, albaran.get('nombre_operario','') if es_devol else albaran.get('obra','')],
    ], colWidths=[4*cm, 13*cm])
    t_cab.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(0,-1),AZUL),
        ('TEXTCOLOR',(0,0),(0,-1),colors.white),
        ('FONTNAME',(0,0),(0,-1),'Helvetica-Bold'),
        ('FONTSIZE',(0,0),(-1,-1),10),
        ('GRID',(0,0),(-1,-1),0.5,colors.lightgrey),
        ('PADDING',(0,0),(-1,-1),7),
        ('ROWBACKGROUNDS',(1,0),(1,-1),[colors.white, GRIS]),
    ]))
    elements.append(t_cab)
    elements.append(Spacer(1, 0.5*cm))

    # Líneas de material
    sec_style = ParagraphStyle('s', fontSize=9, textColor=colors.white,
        backColor=AZUL, fontName='Helvetica-Bold', spaceAfter=0, spaceBefore=4, borderPad=4)
    elements.append(Paragraph(titulo_sec, sec_style))
    elements.append(Spacer(1, 0.1*cm))

    lineas = albaran.get('lineas', [])
    # Comprobar si alguna línea tiene precio
    tiene_precios = any(float(l.get('precio', 0) or 0) > 0 for l in lineas)
    # Signo negativo en devoluciones
    signo = -1 if es_devol else 1

    if tiene_precios:
        filas = [['Material', 'Cantidad', 'Unidad', 'P. Unit. (€)', 'Total (€)']]
        total_general = 0
        for l in lineas:
            cant = float(l.get('cantidad', 0) or 0) * signo
            precio = float(l.get('precio', 0) or 0)
            subtotal = cant * precio
            total_general += subtotal
            filas.append([
                l.get('material',''),
                f"{cant:+g}" if es_devol else f"{cant:g}",
                l.get('unidad',''),
                f"{precio:.2f}" if precio > 0 else '—',
                f"{subtotal:+.2f}" if precio > 0 else '—'
            ])
        filas.append(['', '', '', 'TOTAL', f"{total_general:+.2f} €" if es_devol else f"{total_general:.2f} €"])
        col_widths = [7.5*cm, 2.5*cm, 2*cm, 3*cm, 2*cm]
    else:
        filas = [['Material', 'Cantidad', 'Unidad']]
        for l in lineas:
            cant = float(l.get('cantidad', 0) or 0) * signo
            filas.append([l.get('material',''), f"{cant:+g}" if es_devol else f"{cant:g}", l.get('unidad','')])
        col_widths = [10*cm, 3.5*cm, 3.5*cm]

    VERDE = colors.HexColor('#1a5c3a')
    t_lin = Table(filas, colWidths=col_widths)
    estilo_tabla = [
        ('BACKGROUND',(0,0),(-1,0),AZUL),
        ('TEXTCOLOR',(0,0),(-1,0),colors.white),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
        ('FONTSIZE',(0,0),(-1,-1),10),
        ('GRID',(0,0),(-1,-1),0.5,colors.lightgrey),
        ('PADDING',(0,0),(-1,-1),8),
        ('ROWBACKGROUNDS',(0,1),(-1,-2 if tiene_precios else -1),[colors.white, GRIS]),
        ('ALIGN',(1,0),(-1,-1),'CENTER'),
    ]
    if tiene_precios:
        # Fila de total en negrita con fondo
        estilo_tabla += [
            ('BACKGROUND',(0,-1),(-1,-1),AZUL),
            ('TEXTCOLOR',(0,-1),(-1,-1),colors.white),
            ('FONTNAME',(0,-1),(-1,-1),'Helvetica-Bold'),
            ('SPAN',(0,-1),(2,-1)),
        ]
    t_lin.setStyle(TableStyle(estilo_tabla))
    elements.append(t_lin)
    elements.append(Spacer(1, 1*cm))

    # Firma
    if not es_devol:
        firma_table = Table([
            ['Firma del operario:', ''],
            ['', ''],
        ], colWidths=[8*cm, 9*cm])
        firma_table.setStyle(TableStyle([
            ('FONTNAME',(0,0),(0,0),'Helvetica-Bold'),
            ('FONTSIZE',(0,0),(-1,-1),9),
            ('LINEBELOW',(1,1),(1,1),1,colors.black),
            ('BOTTOMPADDING',(0,0),(-1,-1),18),
        ]))
        elements.append(firma_table)
        elements.append(Spacer(1, 0.3*cm))

    elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey))
    elements.append(Paragraph(f"Generado el {fecha_str} — Instapalma · Almacén", pie_style))
    doc.build(elements)
    return buffer.getvalue()

def subir_pdf_albaran(pdf_bytes, numero):
    try:
        import cloudinary, cloudinary.uploader, base64 as _b64
        cloudinary.config(
            cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME',''),
            api_key=os.environ.get('CLOUDINARY_API_KEY',''),
            api_secret=os.environ.get('CLOUDINARY_API_SECRET','')
        )
        b64 = _b64.b64encode(pdf_bytes).decode()
        result = cloudinary.uploader.upload(
            f"data:application/pdf;base64,{b64}",
            public_id=f"albaran_{numero.replace('-','_')}",
            resource_type='raw', folder='instapalma_albaranes'
        )
        return result.get('secure_url','')
    except Exception as e:
        print(f"Error subir PDF albarán: {e}")
        return ''

# ── Flujo conversacional stock ────────────────────────────────────────────────
# Pasos salida: stock_salida_obra → stock_salida_material → stock_salida_cantidad → (loop) → stock_salida_confirmar
# Pasos devol:  stock_devol_material → stock_devol_cantidad → (loop) → stock_devol_confirmar
# Consulta:     directa, sin pasos

def buscar_material_msg(texto):
    """Devuelve (material_row, mensaje_error_o_None)."""
    r = get_material_by_nombre(texto)
    if r is None:
        return None, "❌ Error al consultar el almacén. Intenta de nuevo."
    if isinstance(r, tuple):
        return r, None  # exacto
    if len(r) == 0:
        return None, f"❌ No encontré *{texto}* en el catálogo. Escribe el nombre exacto o parte de él."
    if len(r) == 1:
        return r[0], None  # único resultado aproximado
    # Varios candidatos — detectar si son retales del mismo cable
    if detectar_retales(r):
        return None, ('RETALES', r)  # señal especial: es un grupo de retales
    lista = '\n'.join([f"• {x[1]} ({x[3]} {x[2]})" for x in r])
    return None, f"🔍 Encontré varios materiales:\n{lista}\n\nEscribe el nombre más completo."


def detectar_retales(candidatos):
    """
    Detecta si una lista de candidatos son retales del mismo material
    (misma raíz de nombre, unidad m/ml/metros, o stock=1 en todos).
    """
    if len(candidatos) < 2:
        return False
    unidades_longitud = {'m', 'ml', 'metros', 'metro'}
    for c in candidatos:
        if str(c[2]).lower() in unidades_longitud:
            return True
    if all(c[3] == 1 for c in candidatos):
        return True
    return False


def sugerir_retales(retales, metros_necesarios):
    """
    Dado un listado de retales (id, nombre, unidad, stock, minimo)
    y los metros necesarios, devuelve sugerencias de combinación óptima.
    """
    import re
    def extraer_metros(r):
        nombre = r[1]
        m = re.search(r'(\d+[.,]?\d*)\s*(ml?|metros?)\b', nombre.lower())
        if m:
            return float(m.group(1).replace(',', '.'))
        m2 = re.search(r'(\d+[.,]?\d*)$', nombre.strip())
        if m2:
            return float(m2.group(1).replace(',', '.'))
        return float(r[3])  # fallback: stock_actual como longitud

    retales_con_m = []
    for r in retales:
        if r[3] <= 0:
            continue
        metros = extraer_metros(r)
        retales_con_m.append({
            'id': r[0], 'nombre': r[1], 'unidad': r[2],
            'metros': metros, 'stock': r[3],
            'precio': float(r[5]) if len(r) > 5 and r[5] else 0
        })

    total_disponible = sum(x['metros'] for x in retales_con_m)

    # Retales individuales que cubren solos (ordenados de menor a mayor desperdicio)
    individuales = sorted(
        [r for r in retales_con_m if r['metros'] >= metros_necesarios],
        key=lambda x: x['metros']
    )

    # Combinación greedy: los más grandes hasta cubrir
    combinacion = []
    if not individuales:
        disponibles = sorted(retales_con_m, key=lambda x: -x['metros'])
        acumulado = 0
        for r in disponibles:
            combinacion.append(r)
            acumulado += r['metros']
            if acumulado >= metros_necesarios:
                break

    return {
        'individuales': individuales,
        'combinacion': combinacion,
        'total_disponible': total_disponible,
        'retales': retales_con_m
    }



# ── Panel web Almacén ─────────────────────────────────────────────────────────

@app.route('/almacen')
def panel_almacen():
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT id, nombre, unidad, stock_actual, stock_minimo, updated_at FROM stock_materiales ORDER BY nombre")
        materiales = cur.fetchall()
        cur.execute("SELECT tipo, material_nombre, cantidad, unidad, nombre_operario, obra, created_at FROM stock_movimientos ORDER BY created_at DESC LIMIT 100")
        movimientos = cur.fetchall()
        cur.close(); conn.close()
    except Exception as e:
        return f"Error: {e}", 500

    filas_mat = ''
    for m in materiales:
        mid, nombre, unidad, stock, minimo, upd = m
        alerta = ' style="background:#fff3e0"' if minimo > 0 and stock <= minimo else ''
        badge = f'<span style="background:#e65100;color:white;padding:2px 8px;border-radius:8px;font-size:11px">⚠️ Bajo mínimo</span>' if minimo > 0 and stock <= minimo else ''
        filas_mat += (
            f"<tr{alerta}>"
            f"<td>{nombre}</td><td style='text-align:center'>{stock}</td>"
            f"<td style='text-align:center'>{unidad}</td>"
            f"<td style='text-align:center'>{minimo}</td>"
            f"<td>{badge}</td>"
            f"<td><a href='/almacen/material/{mid}/editar' style='color:#1a3a5c;font-size:12px'>✏️</a></td>"
            f"</tr>"
        )
    if not filas_mat:
        filas_mat = "<tr><td colspan=6 class='empty'>Sin materiales — sube tu Excel para cargar el catálogo</td></tr>"

    filas_mov = ''
    iconos = {'salida': '📤', 'devolucion': '📥', 'entrada': '➕'}
    for mv in movimientos:
        tipo, mat, cant, unidad, op, obra, fecha = mv
        icono = iconos.get(tipo, '•')
        filas_mov += (
            f"<tr><td>{icono} {tipo.upper()}</td><td>{mat}</td>"
            f"<td style='text-align:center'>{cant} {unidad}</td>"
            f"<td>{op or ''}</td><td>{obra or ''}</td><td>{str(fecha)[:16]}</td></tr>"
        )
    if not filas_mov:
        filas_mov = "<tr><td colspan=6 class='empty'>Sin movimientos</td></tr>"

    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
    <title>Almacén — Instapalma</title>
    <style>{CSS_BASE}
    .upload-box{{background:white;border:2px dashed #1a3a5c;border-radius:10px;padding:24px;margin:0 0 20px;text-align:center}}
    .upload-box input[type=file]{{margin:10px 0}}
    .btn-up{{background:#1a3a5c;color:white;padding:10px 24px;border:none;border-radius:8px;font-weight:700;cursor:pointer;font-size:14px}}
    </style></head><body>
    <header>
      <div><h1>📦 Almacén</h1><p>Instapalma</p></div>
      <a href='/almacen/material/nuevo' style='background:white;color:#1a3a5c;padding:8px 16px;border-radius:8px;font-weight:700;text-decoration:none;font-size:13px'>+ Añadir material</a>
    </header>
    <div class='wrap' style='padding-top:20px'>

    <div class='upload-box'>
      <b style='color:#1a3a5c'>📂 Carga masiva desde Excel</b><br>
      <small>Columnas requeridas: <b>nombre</b>, <b>unidad</b>, <b>stock_actual</b>, <b>stock_minimo</b></small><br>
      <form method='POST' action='/almacen/importar' enctype='multipart/form-data'>
        <input type='file' name='archivo' accept='.xlsx,.xls,.csv' required>
        <button class='btn-up' type='submit'>⬆ Importar</button>
      </form>
    </div>

    <h3 style='color:#1a3a5c;margin-bottom:12px'>Stock de Materiales</h3>
    <table><thead><tr><th>Material</th><th>Stock</th><th>Unidad</th><th>Mínimo</th><th>Estado</th><th></th></tr></thead>
    <tbody>{filas_mat}</tbody></table>

    <h3 style='color:#1a3a5c;margin:24px 0 12px'>Últimos Movimientos</h3>
    <table><thead><tr><th>Tipo</th><th>Material</th><th>Cantidad</th><th>Operario</th><th>Obra</th><th>Fecha</th></tr></thead>
    <tbody>{filas_mov}</tbody></table>

    <div style='margin-top:16px'><a href='/almacen/albaranes' style='color:#1a3a5c;font-weight:700'>📋 Ver albaranes →</a></div>
    </div>
    <div style='padding:0 30px'><a href='/partes' class='back'>← Partes de Trabajo</a></div>
    </body></html>"""


@app.route('/almacen/material/nuevo', methods=['GET','POST'])
@app.route('/almacen/material/<int:mid>/editar', methods=['GET','POST'])
def editar_material(mid=None):
    from flask import request as req
    if req.method == 'POST':
        nombre = req.form.get('nombre','').strip()
        unidad = req.form.get('unidad','ud').strip()
        stock  = float(req.form.get('stock_actual', 0) or 0)
        minimo = float(req.form.get('stock_minimo', 0) or 0)
        precio = float(req.form.get('precio_unitario', 0) or 0)
        try:
            conn = get_db(); cur = conn.cursor()
            if mid:
                cur.execute("UPDATE stock_materiales SET nombre=%s, unidad=%s, stock_actual=%s, stock_minimo=%s, precio_unitario=%s, updated_at=NOW() WHERE id=%s",
                    (nombre, unidad, stock, minimo, precio, mid))
            else:
                cur.execute("INSERT INTO stock_materiales (nombre, unidad, stock_actual, stock_minimo, precio_unitario) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (nombre) DO UPDATE SET unidad=%s, stock_actual=%s, stock_minimo=%s, precio_unitario=%s, updated_at=NOW()",
                    (nombre, unidad, stock, minimo, precio, unidad, stock, minimo, precio))
            conn.commit(); cur.close(); conn.close()
        except Exception as e:
            return f"Error: {e}", 500
        from flask import redirect
        return redirect('/almacen')

    datos = {'nombre':'','unidad':'ud','stock_actual':0,'stock_minimo':0}
    if mid:
        try:
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT nombre, unidad, stock_actual, stock_minimo FROM stock_materiales WHERE id=%s", (mid,))
            r = cur.fetchone(); cur.close(); conn.close()
            if r: datos = {'nombre':r[0],'unidad':r[1],'stock_actual':r[2],'stock_minimo':r[3]}
        except: pass
    titulo = 'Editar material' if mid else 'Nuevo material'
    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'><title>{titulo}</title>
    <style>{CSS_BASE} label{{display:block;margin-bottom:4px;font-size:13px;font-weight:600;color:#555}}
    input{{width:100%;padding:10px;border:1px solid #ddd;border-radius:8px;font-size:14px;margin-bottom:16px;box-sizing:border-box}}
    .btn{{background:#1a3a5c;color:white;padding:12px 28px;border:none;border-radius:8px;font-size:15px;font-weight:700;cursor:pointer}}</style></head><body>
    <header><div><h1>📦 {titulo}</h1><p>Instapalma</p></div></header>
    <div style='max-width:500px;margin:30px auto;background:white;padding:28px;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,.1)'>
    <form method='POST'>
    <label>Nombre del material</label><input name='nombre' value='{datos["nombre"]}' required>
    <label>Unidad (ud, m, ml, kg, rollo...)</label><input name='unidad' value='{datos["unidad"]}' required>
    <label>Stock actual</label><input name='stock_actual' type='number' step='0.001' value='{datos["stock_actual"]}' required>
    <label>Stock mínimo (alerta)</label><input name='stock_minimo' type='number' step='0.001' value='{datos["stock_minimo"]}'>
    <button class='btn' type='submit'>Guardar</button>
    </form></div>
    <div style='padding:0 30px'><a href='/almacen' class='back'>← Volver</a></div>
    </body></html>"""


@app.route('/almacen/importar', methods=['POST'])
def importar_excel():
    from flask import request as req
    archivo = req.files.get('archivo')
    if not archivo:
        return "No se recibió archivo", 400
    try:
        import openpyxl, io as _io, csv
        content = archivo.read()
        nombre_arch = archivo.filename.lower()
        filas = []
        if nombre_arch.endswith('.csv'):
            reader = csv.DictReader(_io.StringIO(content.decode('utf-8-sig')))
            for row in reader:
                filas.append(row)
        else:
            wb = openpyxl.load_workbook(_io.BytesIO(content))
            ws = wb.active
            headers = [str(c.value).strip().lower() if c.value else '' for c in ws[1]]
            for row in ws.iter_rows(min_row=2, values_only=True):
                filas.append(dict(zip(headers, row)))

        conn = get_db(); cur = conn.cursor()
        importados = 0
        for f in filas:
            nombre = str(f.get('nombre',f.get('material',''))).strip()
            if not nombre or nombre.lower() in ['none','nan','']: continue
            unidad = str(f.get('unidad','ud')).strip() or 'ud'
            try: stock = float(str(f.get('stock_actual', f.get('stock',0))).replace(',','.'))
            except: stock = 0
            try: minimo = float(str(f.get('stock_minimo', f.get('minimo',0))).replace(',','.'))
            except: minimo = 0
            try: precio = float(str(f.get('precio_unitario', f.get('precio', f.get('pvp',0)))).replace(',','.'))
            except: precio = 0
            cur.execute("""
                INSERT INTO stock_materiales (nombre, unidad, stock_actual, stock_minimo, precio_unitario)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (nombre) DO UPDATE SET unidad=%s, stock_actual=%s, stock_minimo=%s, precio_unitario=%s, updated_at=NOW()
            """, (nombre, unidad, stock, minimo, precio, unidad, stock, minimo, precio))
            importados += 1
        conn.commit(); cur.close(); conn.close()
        from flask import redirect
        return redirect(f'/almacen?importados={importados}')
    except Exception as e:
        return f"Error importando: {e}", 500


@app.route('/almacen/albaranes')
def panel_albaranes():
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT id, numero, nombre_operario, obra, created_at FROM stock_albaranes ORDER BY created_at DESC LIMIT 200")
        rows = cur.fetchall(); cur.close(); conn.close()
    except Exception as e:
        return f"Error: {e}", 500

    filas = ''
    for r in rows:
        aid, num, op, obra, fecha = r
        filas += (
            f"<tr><td>{num}</td><td>{op or ''}</td><td>{obra or ''}</td>"
            f"<td>{str(fecha)[:16]}</td>"
            f"<td><a href='/almacen/albaranes/{aid}/pdf' style='color:#1a3a5c;font-size:12px'>⬇ PDF</a></td></tr>"
        )
    if not filas:
        filas = "<tr><td colspan=5 class='empty'>Sin albaranes</td></tr>"

    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'><title>Albaranes — Instapalma</title>
    <style>{CSS_BASE}</style></head><body>
    <header><div><h1>📋 Albaranes de Almacén</h1><p>Instapalma</p></div></header>
    <div class='wrap' style='padding-top:20px'>
    <table><thead><tr><th>Nº Albarán</th><th>Operario</th><th>Obra</th><th>Fecha</th><th>PDF</th></tr></thead>
    <tbody>{filas}</tbody></table>
    </div>
    <div style='padding:0 30px'><a href='/almacen' class='back'>← Almacén</a></div>
    </body></html>"""


@app.route('/almacen/albaranes/<int:aid>/pdf')
def pdf_albaran(aid):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT numero, nombre_operario, obra, lineas, created_at FROM stock_albaranes WHERE id=%s", (aid,))
        r = cur.fetchone(); cur.close(); conn.close()
    except Exception as e:
        return f"Error: {e}", 500
    if not r:
        return "No encontrado", 404
    import json as _json
    lineas = r[3] if isinstance(r[3], list) else _json.loads(r[3] or '[]')
    from datetime import datetime as dt
    fecha_str = str(r[4])[:16] if r[4] else dt.now().strftime('%d/%m/%Y %H:%M')
    pdf_bytes = generar_pdf_albaran({'numero': r[0], 'nombre_operario': r[1], 'obra': r[2], 'lineas': lineas, 'fecha': fecha_str})
    nombre_f = f"{r[0]}-{(r[2] or 'OBRA').replace(' ','_')[:30].upper()}.pdf"
    from flask import Response
    return Response(pdf_bytes, mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{nombre_f}"'})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
