"""
Envío del informe operativo diario (PDF) por WhatsApp, como archivo y sin texto.

Módulo nuevo e independiente: no modifica ninguna ruta ni función existente.
Queda DESACTIVADO hasta que se defina la variable de Railway INFORME_PDF_TOKEN.

Rutas:
  POST /api/informe-pdf/enviar         (protegida con cabecera X-Api-Token)
       JSON: {"pdf_base64": "...", "nombre": "informe_operativo_20261117.pdf"}
          o  {"pdf_url": "https://.../informe.pdf", "nombre": "..."}
       Opcional: "to": "+34690875940" (por defecto, el WhatsApp de Alberto).
       Respuesta: {"status": "queued", "sid": "SM...", "media_url": "..."}
  GET  /api/informe-pdf/estado/<sid>   (protegida) estado de entrega en Twilio.
  GET  /informe-pdf/<clave>/<nombre>   sirve el PDF para que Twilio lo descargue
                                        (clave aleatoria; el nombre es el que ve Alberto).
"""

import base64
import hmac
import os
import re
import secrets
import urllib.request

import psycopg2
from flask import Blueprint, Response, request

informe_pdf_bp = Blueprint('informe_pdf', __name__)

DESTINO_POR_DEFECTO = '+34690875940'
MAX_BYTES = 10 * 1024 * 1024        # 10 MB
DIAS_RETENCION = 7


def _db():
    return psycopg2.connect(os.environ.get('DATABASE_URL', ''))


def _asegurar_tabla(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS informes_pdf_wa (
            clave      TEXT PRIMARY KEY,
            nombre     TEXT,
            pdf_bytes  BYTEA NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)


def _autorizado():
    token = os.environ.get('INFORME_PDF_TOKEN', '')
    if not token:
        return None  # desactivado
    recibido = request.headers.get('X-Api-Token', '')
    return hmac.compare_digest(recibido.encode(), token.encode())


def _rechazo():
    ok = _autorizado()
    if ok is None:
        return {'error': 'endpoint desactivado: falta INFORME_PDF_TOKEN'}, 503
    if not ok:
        return {'error': 'no autorizado'}, 401
    return None


def _bot_url():
    return os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'bot-production-66b8.up.railway.app')


@informe_pdf_bp.route('/api/informe-pdf/enviar', methods=['POST'])
def enviar_informe_pdf():
    err = _rechazo()
    if err:
        return err
    data = request.get_json(silent=True) or {}

    # 1) Obtener el PDF
    pdf = None
    if data.get('pdf_base64'):
        try:
            pdf = base64.b64decode(str(data['pdf_base64']), validate=False)
        except Exception:
            return {'error': 'pdf_base64 no válido'}, 400
    elif data.get('pdf_url'):
        url = str(data['pdf_url']).strip()
        if not url.startswith('https://'):
            return {'error': 'pdf_url debe ser https'}, 400
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                pdf = r.read(MAX_BYTES + 1)
        except Exception as e:
            return {'error': f'no se pudo descargar pdf_url: {e}'}, 400
    else:
        return {'error': 'falta pdf_base64 o pdf_url'}, 400

    if len(pdf) > MAX_BYTES:
        return {'error': 'PDF demasiado grande'}, 413
    if not pdf.startswith(b'%PDF'):
        return {'error': 'el archivo no es un PDF'}, 400

    nombre = re.sub(r'[^A-Za-z0-9._-]', '_', str(data.get('nombre') or 'informe.pdf'))[:100]
    if not nombre.lower().endswith('.pdf'):
        nombre += '.pdf'

    destino = re.sub(r'[^\d+]', '', str(data.get('to') or DESTINO_POR_DEFECTO))
    if not destino.startswith('+'):
        destino = '+' + destino

    # 2) Guardar y publicar con una URL no adivinable
    clave = secrets.token_urlsafe(24)
    conn = _db()
    try:
        cur = conn.cursor()
        _asegurar_tabla(cur)
        cur.execute(
            "DELETE FROM informes_pdf_wa WHERE created_at < NOW() - %s * INTERVAL '1 day'",
            (DIAS_RETENCION,))
        cur.execute(
            "INSERT INTO informes_pdf_wa (clave, nombre, pdf_bytes) VALUES (%s, %s, %s)",
            (clave, nombre, psycopg2.Binary(pdf)))
        conn.commit()
        cur.close()
    finally:
        conn.close()
    media_url = f"https://{_bot_url()}/informe-pdf/{clave}/{nombre}"

    # 3) Enviar por WhatsApp solo el archivo (sin texto)
    try:
        from twilio.rest import Client
        client = Client(os.environ.get('TWILIO_ACCOUNT_SID', ''),
                        os.environ.get('TWILIO_AUTH_TOKEN', ''))
        msg = client.messages.create(
            from_=os.environ.get('TWILIO_WA_NUMBER', 'whatsapp:+19784402048'),
            to=f'whatsapp:{destino}',
            media_url=[media_url],
        )
    except Exception as e:
        return {'error': f'Twilio: {e}', 'code': getattr(e, 'code', None),
                'media_url': media_url}, 502
    return {'status': str(msg.status), 'sid': msg.sid, 'to': destino,
            'media_url': media_url}, 200


@informe_pdf_bp.route('/api/informe-pdf/estado/<sid>', methods=['GET'])
def estado_informe_pdf(sid):
    err = _rechazo()
    if err:
        return err
    try:
        from twilio.rest import Client
        client = Client(os.environ.get('TWILIO_ACCOUNT_SID', ''),
                        os.environ.get('TWILIO_AUTH_TOKEN', ''))
        m = client.messages(sid).fetch()
        return {'sid': sid, 'status': str(m.status),
                'error_code': m.error_code, 'error_message': m.error_message}, 200
    except Exception as e:
        return {'error': str(e)}, 502


@informe_pdf_bp.route('/informe-pdf/<clave>/<nombre>', methods=['GET'])
def servir_informe_pdf(clave, nombre):
    if not re.fullmatch(r'[A-Za-z0-9_-]{20,64}', clave):
        return 'No encontrado', 404
    conn = _db()
    try:
        cur = conn.cursor()
        _asegurar_tabla(cur)
        cur.execute("SELECT nombre, pdf_bytes FROM informes_pdf_wa WHERE clave=%s", (clave,))
        row = cur.fetchone()
        conn.commit()
        cur.close()
    finally:
        conn.close()
    if not row:
        return 'No encontrado', 404
    return Response(bytes(row[1]), mimetype='application/pdf',
                    headers={'Content-Disposition': f'inline; filename="{row[0]}"'})
