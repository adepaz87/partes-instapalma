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
            'descripcion': '',
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

    elements.append(Spacer(1, 1*cm))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey))
    elements.append(Paragraph(
        f"Generado el {datetime.now().strftime('%d/%m/%Y %H:%M')} — Instapalma",
        pie_style))

    doc.build(elements)
    buffer.seek(0)
    return buffer.read()

def enviar_email_gmail(datos, numero_operario):
    """Envía el parte por email via Gmail SMTP con PDF adjunto."""
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        print("Gmail no configurado")
        return False
    try:
        pdf_bytes = generar_pdf(datos)
        nombre_pdf = f"parte_{datos['numero_parte']}_{datos['obra'].replace(' ','_')}.pdf"

        msg = MIMEMultipart()
        msg['From'] = GMAIL_USER
        msg['To'] = f"{SUPERVISOR_EMAIL_1}, {SUPERVISOR_EMAIL_2}"
        msg['Subject'] = f"Parte de trabajo — {datos['obra']} — {datos['fecha']}"

        cuerpo = (
            f"Parte de trabajo generado desde WhatsApp.\n\n"
            f"Fecha: {datos['fecha']}\n"
            f"Cliente: {datos['cliente']}\n"
            f"Obra: {datos['obra']}\n"
            f"Operario: {numero_operario}\n\n"
            f"Ver PDF adjunto."
        )
        msg.attach(MIMEText(cuerpo, 'plain'))

        adjunto = MIMEApplication(pdf_bytes, _subtype='pdf')
        adjunto.add_header('Content-Disposition', 'attachment', filename=nombre_pdf)
        msg.attach(adjunto)

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_USER,
                [SUPERVISOR_EMAIL_1, SUPERVISOR_EMAIL_2],
                msg.as_string())
        print("Email enviado correctamente")
        return True
    except Exception as e:
        print(f"Error enviando email: {e}")
        return False

def enviar_whatsapp(destino, mensaje):
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(from_=TWILIO_WA_NUMBER, to=destino, body=mensaje)
    except Exception as e:
        print(f"Error WA: {e}")

def generar_resumen(datos):
    ops = datos.get('operarios', 'Ninguno')
    albs = datos.get('albaranes', 'Ninguno')
    desc = datos.get('descripcion', '-')
    return (
        f"📋 *RESUMEN DEL PARTE*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 Fecha: {datos['fecha']}\n"
        f"🏢 Cliente: {datos['cliente']}\n"
        f"🔨 Obra: {datos['obra']}\n"
        f"👷 Operarios:\n{ops}\n"
        f"📦 Albaranes: {albs}\n"
        f"📝 Descripción: {desc}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"¿Es correcto? Responde *SÍ* para enviar o *NO* para cancelar."
    )

def finalizar_parte(numero, datos):
    ops = datos.get('operarios', 'Ninguno')
    albs = datos.get('albaranes', 'Ninguno')
    desc = datos.get('descripcion', '-')

    msg_supervisor = (
        f"📋 *PARTE DE TRABAJO*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 {datos['fecha']} — Nº {datos['numero_parte']}\n"
        f"📱 Operario: {numero}\n"
        f"🏢 Cliente: {datos['cliente']}\n"
        f"🔨 Obra: {datos['obra']}\n"
        f"👷 Operarios:\n{ops}\n"
        f"📦 Albaranes: {albs}\n"
        f"📝 {desc}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    enviar_whatsapp(SUPERVISOR_WA, msg_supervisor)
    enviar_email_gmail(datos, numero)
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
        set_paso(numero, 'descripcion')
        msg.body("5️⃣ *Descripción* de los trabajos realizados:")

    elif paso == 'descripcion':
        set_dato(numero, 'descripcion', incoming_msg)
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

@app.route('/health', methods=['GET'])
def health():
    return {'status': 'ok', 'service': 'partes-instapalma'}, 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
