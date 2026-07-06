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
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
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
TWILIO_WA_NUMBER   = os.environ.get('TWILIO_WA_NUMBER', 'whatsapp:+14155238886')
SUPERVISOR_EMAIL_1 = 'alberto@adpb.es'
SUPERVISOR_EMAIL_2 = 'adm2@adpb.es'
SUPERVISOR_WA      = os.environ.get('SUPERVISOR_WA', 'whatsapp:+34690875940')
GMAIL_USER         = os.environ.get('GMAIL_USER', '')
GMAIL_APP_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '')

# Estado de conversaciones en memoria
conversaciones = {}

MENSAJES_INICIO = ['parte', 'parte de trabajo', 'nuevo parte', 'abrir parte', 'crear parte', 'hola']

# Contador de partes (simple, en memoria)
_parte_counter = [0]

def get_numero_parte():
    _parte_counter[0] += 1
    return f"{datetime.now().strftime('%Y')}-{_parte_counter[0]:04d}"

def normalizar(texto):
    return texto.strip().lower()

def es_confirmacion(texto):
    return normalizar(texto) in ['si', 'sí', 'ok', 'vale', 'correcto', 'confirmado', 's', 'yes']

def es_cancelacion(texto):
    return normalizar(texto) in ['no', 'cancelar', 'cancel']

def iniciar_parte(numero):
    conversaciones[numero] = {
        'paso': 'cliente',
        'datos': {
            'numero_parte': get_numero_parte(),
            'operario': numero,
            'cliente': '',
            'obra': '',
            'operarios': '',
            'albaranes': '',
            'material_stock': '',
            'descripcion': '',
            'terminado': '',
            'tiempo_restante': '',
            'fecha': datetime.now().strftime('%d/%m/%Y'),
        }
    }

def get_estado(numero):
    return conversaciones.get(numero)

def set_paso(numero, paso):
    if numero in conversaciones:
        conversaciones[numero]['paso'] = paso

def set_dato(numero, clave, valor):
    if numero in conversaciones:
        conversaciones[numero]['datos'][clave] = valor

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

    elements.append(Paragraph("INSTAPALMA", titulo_style))
    elements.append(Paragraph("Parte de Trabajo", sub_style))
    elements.append(HRFlowable(width="100%", thickness=2, color=AZUL))
    elements.append(Spacer(1, 0.4*cm))

    # Cabecera
    t_cab = Table([['Fecha', datos['fecha'], 'Nº Parte', datos['numero_parte']]],
        colWidths=[3*cm, 7*cm, 3*cm, 4*cm])
    t_cab.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,-1), GRIS),
        ('BACKGROUND', (2,0), (2,-1), GRIS),
        ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
        ('FONTNAME', (2,0), (2,-1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('TEXTCOLOR', (0,0), (0,-1), AZUL),
        ('TEXTCOLOR', (2,0), (2,-1), AZUL),
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
    ops_rows = [['Nombre', 'Horas']]
    for linea in datos['operarios'].split('\n'):
        linea = linea.strip()
        if not linea:
            continue
        if '—' in linea:
            parts = linea.split('—', 1)
        elif '-' in linea:
            parts = linea.split('-', 1)
        else:
            parts = [linea, '']
        ops_rows.append([parts[0].strip(), parts[1].strip() if len(parts) > 1 else ''])
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

    # Albaranes
    elements.append(Paragraph("ALBARANES", sec_style))
    if normalizar(datos.get('albaranes', '')) == 'ninguno' or not datos.get('albaranes'):
        alb_rows = [['Proveedor', 'Nº Albarán'], ['—', '—']]
    else:
        alb_rows = [['Proveedor', 'Nº Albarán']]
        for linea in datos['albaranes'].split('\n'):
            linea = linea.strip()
            if not linea:
                continue
            if '—' in linea:
                parts = linea.split('—', 1)
            elif '-' in linea:
                parts = linea.split('-', 1)
            else:
                parts = [linea, '']
            alb_rows.append([parts[0].strip(), parts[1].strip() if len(parts) > 1 else ''])
    t_alb = Table(alb_rows, colWidths=[10*cm, 7*cm])
    t_alb.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), AZUL),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
        ('PADDING', (0,0), (-1,-1), 6),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, GRIS]),
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
            nombre_pdf = f"parte_{datos.get('numero_parte','X')}_{datos.get('obra','obra').replace(' ','_')}.pdf"
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
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        kwargs = dict(from_=TWILIO_WA_NUMBER, to=destino, body=mensaje)
        if media_url:
            kwargs['media_url'] = [media_url]
        client.messages.create(**kwargs)
    except Exception as e:
        print(f"Error WA: {e}")

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
        f"📅 {datos['fecha']} — Nº {datos['numero_parte']}\n"
        f"📱 Operario: {numero}\n"
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

    # Generar PDF en memoria para adjuntar al email
    pdf_bytes = generar_pdf(datos)

    # Enviar email con PDF adjunto directamente desde Railway
    enviar_email_gmail(datos, numero, pdf_bytes=pdf_bytes)

    # Enviar PDF directamente a tu WhatsApp como archivo adjunto
    BOT_URL = os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'bot-production-66b8.up.railway.app')
    if parte_id:
        pdf_url = f"https://{BOT_URL}/partes/{parte_id}/pdf"
        caption = (
            f"📄 *Parte Nº {datos['numero_parte']}* — {datos['fecha']}\n"
            f"🏢 {datos['cliente']} | 🔨 {datos['obra']}\n"
            f"🏁 {linea_term}"
        )
        enviar_whatsapp(SUPERVISOR_WA, caption, media_url=pdf_url)
        # Enviar copia del PDF también al operario
        enviar_whatsapp(f"whatsapp:{numero}" if not numero.startswith("whatsapp:") else numero,
                        f"✅ *Parte Nº {datos['numero_parte']} confirmado*\nAquí tienes tu copia en PDF:",
                        media_url=pdf_url)

    # Notificar a Zapia para que envíe al grupo Instapalma
    payload = json.dumps({
        "tipo": "PARTE_CONFIRMADO",
        "numero_parte": datos['numero_parte'],
        "fecha": datos['fecha'],
        "operario": numero,
        "cliente": datos['cliente'],
        "obra": datos['obra'],
        "operarios": ops,
        "albaranes": albs,
        "material_stock": mat,
        "descripcion": desc,
        "terminado": linea_term,
        "enviar_grupo_instapalma": True,
        "grupo_jid": "34690875940-1553511485@g.us",
        "pdf_url": f"https://{BOT_URL}/partes/{parte_id}/pdf" if parte_id else None
    }, ensure_ascii=False)
    enviar_whatsapp(SUPERVISOR_WA, f"[ZAPIA_PDF]{payload}[/ZAPIA_PDF]")

    del conversaciones[numero]

@app.route('/webhook', methods=['POST'])
def webhook():
    incoming_msg = request.form.get('Body', '').strip()
    numero = request.form.get('From', '')

    resp = MessagingResponse()
    msg = resp.message()
    estado = get_estado(numero)

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
        # Validar que cada línea tenga horas (número seguido de h, hrs, horas, etc.)
        import re
        lineas = [l.strip() for l in incoming_msg.strip().split('\n') if l.strip()]
        sin_horas = []
        for linea in lineas:
            if not re.search(r'\d+\s*h', linea, re.IGNORECASE):
                sin_horas.append(linea)
        if sin_horas:
            lista = '\n'.join(f'• {l}' for l in sin_horas)
            msg.body(
                f"⚠️ Faltan las *horas* en:\n{lista}\n\n"
                "Escríbelo así:\n"
                "_NOMBRE — 8h_\n\n"
                "Vuelve a escribir todos los operarios con sus horas:"
            )
        else:
            set_dato(numero, 'operarios', incoming_msg)
            set_paso(numero, 'albaranes')
            msg.body(
                "4️⃣ *Albaranes*\n\n"
                "Escribe los albaranes, uno por línea:\n"
                "_Ejemplo:_\n"
                "DIEXFE — 3547364\n\n"
                "Si no hay, escribe: *ninguno*"
            )

    elif paso == 'albaranes':
        val = incoming_msg if normalizar(incoming_msg) != 'ninguno' else 'Ninguno'
        set_dato(numero, 'albaranes', val)
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
            msg.body(generar_resumen(conversaciones[numero]['datos']))
        elif normalizar(incoming_msg) in ['no', 'n']:
            set_dato(numero, 'terminado', 'No')
            set_paso(numero, 'tiempo_restante')
            msg.body("8️⃣ ¿Cuánto tiempo queda para terminarlo?\n\n_Ejemplo: 2 días, media jornada, 3 horas..._")
        else:
            msg.body("Responde *SÍ* si está terminado o *NO* si falta trabajo.")

    elif paso == 'tiempo_restante':
        set_dato(numero, 'tiempo_restante', incoming_msg)
        set_paso(numero, 'confirmar')
        msg.body(generar_resumen(conversaciones[numero]['datos']))

    elif paso == 'confirmar':
        if es_confirmacion(incoming_msg):
            finalizar_parte(numero, datos)
            msg.body(
                "✅ *Parte enviado correctamente.*\n\n"
                "Se ha notificado al supervisor por WhatsApp y email con PDF. ¡Gracias!"
            )
        elif es_cancelacion(incoming_msg):
            del conversaciones[numero]
            msg.body("❌ Parte cancelado. Escribe *parte* para crear uno nuevo.")
        else:
            msg.body("Responde *SÍ* para confirmar y enviar, o *NO* para cancelar.")

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
        operario_limpio = (r[3] or '').replace('whatsapp:','').replace('+34','')
        descargado = r[9]
        desc_at = str(r[10])[:16].replace('T',' ') if r[10] else ''
        if descargado:
            pdf_badge = f'<span title="Descargado {desc_at}" style="color:#2e7d32;font-size:18px" title="{desc_at}">⬇️</span>'
        else:
            pdf_badge = '<span style="color:#ccc;font-size:18px">—</span>'
        filas += f'<tr class="clickable" onclick="window.location=\'/partes/{r[0]}\'">' \
                 f'<td><strong>{r[1] or ""}</strong></td>' \
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
      <th>Nº Parte</th><th>Fecha</th><th>Operario</th><th>Cliente</th><th>Obra</th><th>Estado</th><th>PDF</th>
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
    operario_limpio = (r[3] or '').replace('whatsapp:','')

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
    <h2>Parte Nº {r[1]}</h2>
    <p>Fecha: {r[2] or '—'} &nbsp;·&nbsp; Registrado: {str(r[12])[:16] if r[12] else '—'}</p>
  </div>
  <div class="ficha-body">
    <div class="grid2">
      <div class="campo"><label>Cliente</label><div class="val">{r[4] or '—'}</div></div>
      <div class="campo"><label>Obra</label><div class="val">{r[5] or '—'}</div></div>
      <div class="campo"><label>Operario (WhatsApp)</label><div class="val">{operario_limpio}</div></div>
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
    nombre = f"parte_{r[1]}_{(r[5] or 'obra').replace(' ','_')}.pdf"
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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
