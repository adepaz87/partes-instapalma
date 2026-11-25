"""
Avisos por WhatsApp a trabajadores y clientes, enviados desde otro sistema
(el proyecto de mantenimiento de Claude) a través del número del bot.

Módulo nuevo e independiente: no modifica ninguna ruta ni función existente.
Queda DESACTIVADO hasta que se defina la variable de Railway AVISOS_TOKEN.

Todas las rutas /api/avisos/* exigen la cabecera  X-Api-Token: <AVISOS_TOKEN>.

POST /api/avisos/enviar
  JSON:
    "to":       "+34616233640"  o una lista de números (máx. 10).
                Un número de 9 cifras se toma como español (+34).
    "texto":    texto libre del aviso (máx. 1600 caracteres).
    "pdf_base64" o "pdf_url" (https):  PDF opcional que va adjunto.
    "nombre":   nombre del PDF que verá el destinatario (opcional).
    "plantilla": {"nombre": "ot_cerrada", "variables": {"1": "...", "2": "..."}}
                (o {"sid": "HX...", ...}). Opcional. Plantilla aprobada de WhatsApp
                (ver plantillas.py). Con documento_mantenimiento se manda también
                el PDF y el bot rellena la variable 4. Es lo que hay que usar
                con quien no haya escrito al bot en las últimas 24 h (clientes),
                porque WhatsApp no deja mandarles texto libre.
  Respuesta: {"resultados": [{"to": "+34...", "status": "queued", "sid": "SM..."},
                             {"to": "+34...", "error": "...", "code": 63016,
                              "ventana_cerrada": true}]}
  Código 200 si al menos uno salió; 502 si fallaron todos.

GET /api/avisos/estado/<sid>
  Estado de entrega en Twilio. Si error_code es 63016, la ventana de 24 h estaba
  cerrada: hay que repetir el aviso con una plantilla.

GET /avisos-pdf/<clave>/<nombre>
  Sirve el PDF adjunto para que Twilio lo descargue (clave aleatoria, 7 días).
"""

import base64
import hmac
import json
import os
import re
import secrets
import urllib.request

import psycopg2
from flask import Blueprint, Response, request

avisos_bp = Blueprint('avisos', __name__)

MAX_BYTES = 10 * 1024 * 1024        # 10 MB, límite de Twilio para un PDF
MAX_TEXTO = 1600                    # límite de WhatsApp por mensaje
MAX_DESTINOS = 10
DIAS_RETENCION = 7
ERROR_VENTANA_CERRADA = 63016


def _db():
    return psycopg2.connect(os.environ.get('DATABASE_URL', ''))


def _asegurar_tabla(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS avisos_pdf_wa (
            clave      TEXT PRIMARY KEY,
            nombre     TEXT,
            pdf_bytes  BYTEA NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)


def _rechazo():
    token = os.environ.get('AVISOS_TOKEN', '')
    if not token:
        return {'error': 'avisos desactivados: falta AVISOS_TOKEN'}, 503
    recibido = request.headers.get('X-Api-Token', '')
    if not hmac.compare_digest(recibido.encode(), token.encode()):
        return {'error': 'no autorizado'}, 401
    return None


def _bot_url():
    return os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'bot-production-66b8.up.railway.app')


def _twilio():
    from twilio.rest import Client
    return Client(os.environ.get('TWILIO_ACCOUNT_SID', ''),
                  os.environ.get('TWILIO_AUTH_TOKEN', ''))


def normalizar_numero(valor):
    """Devuelve '+<cifras>' o None si no parece un móvil válido."""
    s = str(valor or '').strip()
    if s.lower().startswith('whatsapp:'):
        s = s[len('whatsapp:'):]
    s = re.sub(r'[\s().-]', '', s)
    if s.startswith('00'):
        s = '+' + s[2:]
    if re.fullmatch(r'\d{9}', s):
        s = '+34' + s
    if not s.startswith('+'):
        s = '+' + s
    if not re.fullmatch(r'\+\d{8,15}', s):
        return None
    return s


def _leer_pdf(data):
    """Devuelve (bytes, None) o (None, (error, código))."""
    if data.get('pdf_base64'):
        try:
            pdf = base64.b64decode(str(data['pdf_base64']), validate=False)
        except Exception:
            return None, ({'error': 'pdf_base64 no válido'}, 400)
    elif data.get('pdf_url'):
        url = str(data['pdf_url']).strip()
        if not url.startswith('https://'):
            return None, ({'error': 'pdf_url debe ser https'}, 400)
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                pdf = r.read(MAX_BYTES + 1)
        except Exception as e:
            return None, ({'error': f'no se pudo descargar pdf_url: {e}'}, 400)
    else:
        return None, None
    if len(pdf) > MAX_BYTES:
        return None, ({'error': 'PDF demasiado grande'}, 413)
    if not pdf.startswith(b'%PDF'):
        return None, ({'error': 'el archivo no es un PDF'}, 400)
    return pdf, None


def _publicar_pdf(pdf, nombre):
    clave = secrets.token_urlsafe(24)
    conn = _db()
    try:
        cur = conn.cursor()
        _asegurar_tabla(cur)
        cur.execute(
            "DELETE FROM avisos_pdf_wa WHERE created_at < NOW() - %s * INTERVAL '1 day'",
            (DIAS_RETENCION,))
        cur.execute(
            "INSERT INTO avisos_pdf_wa (clave, nombre, pdf_bytes) VALUES (%s, %s, %s)",
            (clave, nombre, psycopg2.Binary(pdf)))
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return f"https://{_bot_url()}/avisos-pdf/{clave}/{nombre}"


@avisos_bp.route('/api/avisos/enviar', methods=['POST'])
def enviar_aviso():
    err = _rechazo()
    if err:
        return err
    data = request.get_json(silent=True) or {}

    # Destinatarios
    to = data.get('to')
    lista = to if isinstance(to, list) else [to]
    destinos, malos = [], []
    for n in lista:
        norm = normalizar_numero(n)
        if norm is None:
            malos.append(str(n))
        elif norm not in destinos:
            destinos.append(norm)
    if malos:
        return {'error': 'número no válido', 'numeros': malos}, 400
    if not destinos:
        return {'error': 'falta to'}, 400
    if len(destinos) > MAX_DESTINOS:
        return {'error': f'máximo {MAX_DESTINOS} destinatarios por aviso'}, 400

    # Contenido: plantilla aprobada o texto libre (+ PDF opcional)
    texto = str(data.get('texto') or '').strip()
    plantilla = data.get('plantilla') or None
    con_pdf_en_plantilla = False
    if plantilla is not None:
        if not isinstance(plantilla, dict):
            return {'error': 'plantilla debe ser un objeto'}, 400
        variables = plantilla.get('variables') or {}
        if not isinstance(variables, dict):
            return {'error': 'plantilla.variables debe ser un objeto'}, 400
        plantilla = {'sid': plantilla.get('sid'), 'nombre': plantilla.get('nombre'),
                     'variables': {str(k): str(v) for k, v in variables.items()}}
        if plantilla['nombre'] and not plantilla['sid']:
            from plantillas import sid_de_plantilla, es_plantilla_con_pdf, ErrorTwilio
            try:
                plantilla['sid'] = sid_de_plantilla(str(plantilla['nombre']))
            except ErrorTwilio as e:
                return {'error': f'no se pudo consultar la plantilla: {e}'}, 502
            if not plantilla['sid']:
                return {'error': f"la plantilla {plantilla['nombre']} no existe; créala con /api/avisos/plantillas/crear"}, 400
            con_pdf_en_plantilla = es_plantilla_con_pdf(str(plantilla['nombre']))
        if not re.fullmatch(r'HX[0-9a-fA-F]{32}', str(plantilla['sid'] or '')):
            return {'error': 'plantilla.sid no válido (HX...) o falta plantilla.nombre'}, 400
    if len(texto) > MAX_TEXTO:
        return {'error': f'texto demasiado largo (máx. {MAX_TEXTO})'}, 400

    pdf, err_pdf = _leer_pdf(data)
    if err_pdf:
        return err_pdf
    if not texto and pdf is None and plantilla is None:
        return {'error': 'falta texto, PDF o plantilla'}, 400

    media_url = None
    if pdf is not None:
        nombre = re.sub(r'[^A-Za-z0-9._-]', '_', str(data.get('nombre') or 'aviso.pdf'))[:100]
        if not nombre.lower().endswith('.pdf'):
            nombre += '.pdf'
        media_url = _publicar_pdf(pdf, nombre)
    if con_pdf_en_plantilla:
        from plantillas import VARIABLE_PDF
        if not media_url:
            return {'error': f"la plantilla {plantilla['nombre']} necesita el PDF (pdf_base64 o pdf_url)"}, 400
        plantilla['variables'][VARIABLE_PDF] = media_url.split('/avisos-pdf/', 1)[1]

    kwargs = {'from_': os.environ.get('TWILIO_WA_NUMBER', 'whatsapp:+19784402048')}
    if plantilla is not None:
        kwargs['content_sid'] = plantilla['sid']
        if plantilla['variables']:
            kwargs['content_variables'] = json.dumps(plantilla['variables'], ensure_ascii=False)
    else:
        if texto:
            kwargs['body'] = texto
        if media_url:
            kwargs['media_url'] = [media_url]

    client = _twilio()
    resultados = []
    for destino in destinos:
        try:
            msg = client.messages.create(to=f'whatsapp:{destino}', **kwargs)
            resultados.append({'to': destino, 'status': str(msg.status), 'sid': msg.sid})
        except Exception as e:
            code = getattr(e, 'code', None)
            r = {'to': destino, 'error': str(e), 'code': code}
            if str(code) == str(ERROR_VENTANA_CERRADA):
                r['ventana_cerrada'] = True
            resultados.append(r)
        print(f"Aviso WA -> {destino}: {resultados[-1].get('status') or resultados[-1].get('error')}",
              flush=True)

    algun_ok = any('sid' in r for r in resultados)
    respuesta = {'resultados': resultados}
    if media_url:
        respuesta['media_url'] = media_url
    return respuesta, (200 if algun_ok else 502)


@avisos_bp.route('/api/avisos/estado/<sid>', methods=['GET'])
def estado_aviso(sid):
    err = _rechazo()
    if err:
        return err
    if not re.fullmatch(r'(SM|MM)[0-9a-fA-F]{32}', sid):
        return {'error': 'sid no válido'}, 400
    try:
        m = _twilio().messages(sid).fetch()
        r = {'sid': sid, 'to': m.to, 'status': str(m.status),
             'error_code': m.error_code, 'error_message': m.error_message}
        if str(m.error_code) == str(ERROR_VENTANA_CERRADA):
            r['ventana_cerrada'] = True
        return r, 200
    except Exception as e:
        return {'error': str(e)}, 502


@avisos_bp.route('/avisos-pdf/<clave>/<nombre>', methods=['GET'])
def servir_pdf_aviso(clave, nombre):
    if not re.fullmatch(r'[A-Za-z0-9_-]{20,64}', clave):
        return 'No encontrado', 404
    conn = _db()
    try:
        cur = conn.cursor()
        _asegurar_tabla(cur)
        cur.execute("SELECT nombre, pdf_bytes FROM avisos_pdf_wa WHERE clave=%s", (clave,))
        row = cur.fetchone()
        conn.commit()
        cur.close()
    finally:
        conn.close()
    if not row:
        return 'No encontrado', 404
    return Response(bytes(row[1]), mimetype='application/pdf',
                    headers={'Content-Disposition': f'inline; filename="{row[0]}"'})
