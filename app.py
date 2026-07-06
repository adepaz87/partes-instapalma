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
import re
from datetime import datetime
import tempfile
import subprocess

app = Flask(__name__)

# ── Configuración ─────────────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID', '')
TWILIO_AUTH_TOKEN  = os.environ.get('TWILIO_AUTH_TOKEN', '')
TWILIO_WA_NUMBER   = os.environ.get('TWILIO_WA_NUMBER', 'whatsapp:+14155238886')
SUPERVISOR_EMAIL_1 = 'alberto@adpb.es'
SUPERVISOR_EMAIL_2 = 'adm2@adpb.es'
SUPERVISOR_WA      = os.environ.get('SUPERVISOR_WA', 'whatsapp:+34690875940')
SENDGRID_API_KEY   = os.environ.get('SENDGRID_API_KEY', '')

# Estado de conversaciones en memoria (en producción usar Redis o DB)
# { numero: { paso, datos } }
conversaciones = {}

PASOS = [
    'inicio',
    'cliente',
    'obra',
    'operarios',
    'albaranes',
    'descripcion',
    'confirmar',
]

MENSAJES_INICIO = ['parte', 'parte de trabajo', 'nuevo parte', 'abrir parte', 'crear parte', 'hola']

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
            'operario_nombre': '',
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

def enviar_whatsapp(destino, mensaje):
    """Envía mensaje WhatsApp via Twilio."""
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(
            from_=TWILIO_WA_NUMBER,
            to=destino,
            body=mensaje
        )
    except Exception as e:
        print(f"Error enviando WA a {destino}: {e}")

def enviar_email_parte(datos, numero_operario):
    """Envía el parte por email usando SendGrid."""
    if not SENDGRID_API_KEY:
        print("Sin SendGrid configurado, omitiendo email")
        return

    ops = datos.get('operarios', 'Ninguno')
    albs = datos.get('albaranes', 'Ninguno')
    desc = datos.get('descripcion', '-')

    cuerpo = f"""PARTE DE TRABAJO — INSTAPALMA
==============================
Fecha:       {datos['fecha']}
Operario:    {numero_operario}
Cliente:     {datos['cliente']}
Obra:        {datos['obra']}

OPERARIOS Y HORAS:
{ops}

ALBARANES:
{albs}

DESCRIPCIÓN:
{desc}
"""

    import urllib.request
    import urllib.error

    payload = json.dumps({
        "personalizations": [
            {"to": [{"email": SUPERVISOR_EMAIL_1}, {"email": SUPERVISOR_EMAIL_2}]}
        ],
        "from": {"email": "partes@instapalma.com", "name": "Partes Instapalma"},
        "subject": f"Parte de trabajo — {datos['obra']} — {datos['fecha']}",
        "content": [{"type": "text/plain", "value": cuerpo}]
    }).encode()

    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=payload,
        headers={
            "Authorization": f"Bearer {SENDGRID_API_KEY}",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    try:
        urllib.request.urlopen(req)
        print("Email enviado correctamente")
    except Exception as e:
        print(f"Error enviando email: {e}")

def finalizar_parte(numero, datos):
    """Envía el parte al supervisor por WA y email."""
    ops = datos.get('operarios', 'Ninguno')
    albs = datos.get('albaranes', 'Ninguno')
    desc = datos.get('descripcion', '-')

    msg_supervisor = (
        f"📋 *PARTE DE TRABAJO*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 {datos['fecha']}\n"
        f"📱 Operario: {numero}\n"
        f"🏢 Cliente: {datos['cliente']}\n"
        f"🔨 Obra: {datos['obra']}\n"
        f"👷 Operarios:\n{ops}\n"
        f"📦 Albaranes: {albs}\n"
        f"📝 {desc}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    enviar_whatsapp(SUPERVISOR_WA, msg_supervisor)
    enviar_email_parte(datos, numero)
    del conversaciones[numero]

@app.route('/webhook', methods=['POST'])
def webhook():
    incoming_msg = request.form.get('Body', '').strip()
    numero = request.form.get('From', '')  # ej: whatsapp:+34666123020

    resp = MessagingResponse()
    msg = resp.message()

    estado = get_estado(numero)

    # ── Sin estado: detectar si quiere iniciar parte ──────────────────────────
    if not estado:
        if any(p in normalizar(incoming_msg) for p in MENSAJES_INICIO):
            iniciar_parte(numero)
            msg.body(
                "👷 *Bot de Partes de Trabajo — Instapalma*\n\n"
                "Vamos a crear tu parte paso a paso.\n\n"
                "1️⃣ ¿Cuál es el *cliente*?"
            )
        else:
            msg.body(
                "Hola 👋 Para crear un parte de trabajo escribe: *parte*"
            )
        return str(resp)

    paso = estado['paso']
    datos = estado['datos']

    # ── Paso 1: Cliente ───────────────────────────────────────────────────────
    if paso == 'cliente':
        set_dato(numero, 'cliente', incoming_msg.upper())
        set_paso(numero, 'obra')
        msg.body("2️⃣ ¿Cuál es la *obra*?")

    # ── Paso 2: Obra ──────────────────────────────────────────────────────────
    elif paso == 'obra':
        set_dato(numero, 'obra', incoming_msg.upper())
        set_paso(numero, 'operarios')
        msg.body(
            "3️⃣ *Operarios y horas*\n\n"
            "Escribe cada operario en una línea con su nombre y horas:\n"
            "_Ejemplo:_\n"
            "JORGE GARCIA — 8h\n"
            "ANTONIO JAVIER — 6h\n\n"
            "Si solo eres tú, escribe tu nombre y horas."
        )

    # ── Paso 3: Operarios ─────────────────────────────────────────────────────
    elif paso == 'operarios':
        set_dato(numero, 'operarios', incoming_msg)
        set_paso(numero, 'albaranes')
        msg.body(
            "4️⃣ *Albaranes*\n\n"
            "Escribe los albaranes (proveedor y número), uno por línea:\n"
            "_Ejemplo:_\n"
            "DIEXFE — 3547364\n"
            "REXEL — 12345\n\n"
            "Si no hay albaranes escribe: *ninguno*"
        )

    # ── Paso 4: Albaranes ─────────────────────────────────────────────────────
    elif paso == 'albaranes':
        val = incoming_msg if normalizar(incoming_msg) != 'ninguno' else 'Ninguno'
        set_dato(numero, 'albaranes', val)
        set_paso(numero, 'descripcion')
        msg.body(
            "5️⃣ *Descripción de los trabajos realizados*\n\n"
            "Escribe brevemente qué se ha hecho hoy."
        )

    # ── Paso 5: Descripción ───────────────────────────────────────────────────
    elif paso == 'descripcion':
        set_dato(numero, 'descripcion', incoming_msg)
        set_paso(numero, 'confirmar')
        msg.body(generar_resumen(conversaciones[numero]['datos']))

    # ── Paso 6: Confirmar ─────────────────────────────────────────────────────
    elif paso == 'confirmar':
        if es_confirmacion(incoming_msg):
            finalizar_parte(numero, datos)
            msg.body(
                "✅ *Parte enviado correctamente.*\n\n"
                "Se ha notificado al supervisor. ¡Gracias!"
            )
        elif es_cancelacion(incoming_msg):
            del conversaciones[numero]
            msg.body(
                "❌ Parte cancelado.\n\n"
                "Escribe *parte* cuando quieras crear uno nuevo."
            )
        else:
            msg.body(
                "Por favor responde *SÍ* para confirmar y enviar, o *NO* para cancelar."
            )

    return str(resp)

@app.route('/health', methods=['GET'])
def health():
    return {'status': 'ok', 'service': 'partes-instapalma'}, 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
