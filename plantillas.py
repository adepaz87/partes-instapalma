"""
Plantillas de WhatsApp para avisos a clientes (Content API de Twilio).

WhatsApp solo deja mandar texto libre a quien escribió al bot en las últimas
24 h. Para avisar a clientes que no escriben al bot hacen falta plantillas
aprobadas por WhatsApp. Este módulo las crea con las credenciales de Twilio
que ya tiene el bot y las manda a aprobar, sin pasar por la consola de Twilio.

Rutas (cabecera X-Api-Token = AVISOS_TOKEN, igual que /api/avisos/*):

POST /api/avisos/plantillas/crear
  Crea las plantillas que falten de PLANTILLAS y pide su aprobación a WhatsApp
  (categoría UTILITY, idioma es). Se puede repetir sin duplicar nada: las que
  ya existen solo se mandan a aprobar si aún no se había pedido.
  JSON opcional: {"solo": ["ot_asignada"]} para limitarlo a algunas.

GET /api/avisos/plantillas
  Estado de cada plantilla: sid (HX...) y estado de aprobación en WhatsApp
  (received, pending, approved, rejected...) con el motivo si la rechazan.

Uso desde /api/avisos/enviar:
  "plantilla": {"nombre": "ot_cerrada", "variables": {"1": "...", ...}}
  Para documento_mantenimiento basta con mandar el PDF y las variables 1 a 3:
  la variable 4 (ruta del PDF) la rellena el bot.
"""

import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import Blueprint, request

plantillas_bp = Blueprint('plantillas', __name__)

CONTENT_API = 'https://content.twilio.com/v1'
CATEGORIA = 'UTILITY'
IDIOMA = 'es'


def _bot_url():
    return os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'bot-production-66b8.up.railway.app')


# Variable de la ruta del PDF en la plantilla con documento.
VARIABLE_PDF = '4'

PLANTILLAS = {
    'ot_asignada': {
        'body': ('Nueva orden de trabajo OT {{1}} en {{2}}: {{3}}. '
                 'Asignada a {{4}}. Le informaremos cuando quede resuelta.'),
        'ejemplo': {'1': '172', '2': 'SPAR Mederos',
                    '3': 'Revisión del cuadro eléctrico', '4': 'Toño Guardia'},
    },
    'ot_cerrada': {
        # WhatsApp no admite que el texto termine en una variable, por eso
        # lleva la frase final.
        'body': ('La orden de trabajo OT {{1}} en {{2}} ha quedado resuelta el {{3}}. '
                 'Trabajo realizado: {{4}}. Gracias por su confianza.'),
        'ejemplo': {'1': '172', '2': 'SPAR Mederos', '3': '25/11/2026',
                    '4': 'Sustitución del diferencial del cuadro general'},
    },
    'documento_mantenimiento': {
        'body': 'Le enviamos el {{1}} de {{2}} con fecha {{3}}. Tiene el documento adjunto.',
        'media': True,
        'ejemplo': {'1': 'parte de revisión del grupo electrógeno',
                    '2': 'SPAR El Paso', '3': '25/11/2026'},
    },
}


# ── Llamadas a la Content API ────────────────────────────────────────────────

class ErrorTwilio(Exception):
    def __init__(self, status, detalle):
        super().__init__(f'Twilio {status}: {detalle}')
        self.status = status
        self.detalle = detalle


def _api(metodo, ruta, cuerpo=None):
    sid = os.environ.get('TWILIO_ACCOUNT_SID', '')
    token = os.environ.get('TWILIO_AUTH_TOKEN', '')
    url = ruta if ruta.startswith('https://') else CONTENT_API + ruta
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(url, data=datos, method=metodo)
    req.add_header('Authorization', 'Basic ' + base64.b64encode(f'{sid}:{token}'.encode()).decode())
    if datos is not None:
        req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            texto = r.read().decode() or '{}'
            return json.loads(texto)
    except urllib.error.HTTPError as e:
        detalle = e.read().decode(errors='replace')[:500]
        raise ErrorTwilio(e.code, detalle)


def _contenidos():
    """Todas las plantillas de la cuenta con su aprobación: {friendly_name: item}."""
    res, url = {}, '/ContentAndApprovals?PageSize=100'
    while url:
        page = _api('GET', url)
        for item in page.get('contents', []):
            res[item.get('friendly_name')] = item
        url = (page.get('meta') or {}).get('next_page_url')
    return res


def _estado_whatsapp(item):
    ap = (item or {}).get('approval_requests') or {}
    return {'estado': ap.get('status') or 'sin pedir',
            'motivo': ap.get('rejection_reason') or None}


_cache_sid = {}


def sid_de_plantilla(nombre):
    """HX... de una plantilla por su nombre (None si no existe)."""
    if nombre in _cache_sid and time.time() - _cache_sid[nombre][1] < 600:
        return _cache_sid[nombre][0]
    item = _contenidos().get(nombre)
    sid = item.get('sid') if item else None
    if sid:
        _cache_sid[nombre] = (sid, time.time())
    return sid


def es_plantilla_con_pdf(nombre):
    return bool(PLANTILLAS.get(nombre, {}).get('media'))


# ── Creación ─────────────────────────────────────────────────────────────────

def _pdf_de_ejemplo():
    """Publica un PDF de muestra para que WhatsApp revise la plantilla con documento."""
    import io
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from avisos import _publicar_pdf
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont('Helvetica-Bold', 16)
    c.drawString(72, 770, 'Instapalma - Documento de mantenimiento')
    c.setFont('Helvetica', 11)
    c.drawString(72, 745, 'Ejemplo de parte de revisión adjunto a un aviso.')
    c.save()
    url = _publicar_pdf(buf.getvalue(), 'documento_mantenimiento.pdf')
    return url.split('/avisos-pdf/', 1)[1]          # "<clave>/<nombre>"


def _cuerpo_contenido(nombre, definicion, ejemplo_pdf=None):
    variables = dict(definicion['ejemplo'])
    if definicion.get('media'):
        variables[VARIABLE_PDF] = ejemplo_pdf
        tipos = {'twilio/media': {
            'body': definicion['body'],
            'media': [f'https://{_bot_url()}/avisos-pdf/{{{{{VARIABLE_PDF}}}}}'],
        }}
    else:
        tipos = {'twilio/text': {'body': definicion['body']}}
    return {'friendly_name': nombre, 'language': IDIOMA,
            'variables': variables, 'types': tipos}


def _pedir_aprobacion(sid, nombre):
    return _api('POST', f'/Content/{sid}/ApprovalRequests/whatsapp',
                {'name': nombre, 'category': CATEGORIA})


@plantillas_bp.route('/api/avisos/plantillas/crear', methods=['POST'])
def crear_plantillas():
    from avisos import _rechazo
    err = _rechazo()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    solo = data.get('solo') or list(PLANTILLAS)
    desconocidas = [n for n in solo if n not in PLANTILLAS]
    if desconocidas:
        return {'error': 'plantilla desconocida', 'nombres': desconocidas}, 400

    try:
        existentes = _contenidos()
    except ErrorTwilio as e:
        return {'error': str(e)}, 502

    resultados = []
    for nombre in solo:
        r = {'nombre': nombre}
        try:
            item = existentes.get(nombre)
            if item:
                sid = item['sid']
                r['creada'] = False
            else:
                ejemplo_pdf = _pdf_de_ejemplo() if PLANTILLAS[nombre].get('media') else None
                nuevo = _api('POST', '/Content', _cuerpo_contenido(nombre, PLANTILLAS[nombre], ejemplo_pdf))
                sid = nuevo['sid']
                r['creada'] = True
            r['sid'] = sid
            estado = _estado_whatsapp(item)['estado'] if item else 'sin pedir'
            if estado in ('sin pedir', 'unsubmitted'):
                ap = _pedir_aprobacion(sid, nombre)
                r['aprobacion'] = ap.get('status') or 'received'
            else:
                r['aprobacion'] = estado
            _cache_sid[nombre] = (sid, time.time())
        except ErrorTwilio as e:
            r['error'] = str(e)
        except Exception as e:
            r['error'] = f'{type(e).__name__}: {e}'
        print(f"Plantilla WA {nombre}: {r.get('aprobacion') or r.get('error')}", flush=True)
        resultados.append(r)

    algun_ok = any('sid' in r and 'error' not in r for r in resultados)
    return {'resultados': resultados}, (200 if algun_ok else 502)


@plantillas_bp.route('/api/avisos/plantillas', methods=['GET'])
def estado_plantillas():
    from avisos import _rechazo
    err = _rechazo()
    if err:
        return err
    try:
        existentes = _contenidos()
    except ErrorTwilio as e:
        return {'error': str(e)}, 502
    res = []
    for nombre, definicion in PLANTILLAS.items():
        item = existentes.get(nombre)
        fila = {'nombre': nombre, 'texto': definicion['body'],
                'con_pdf': bool(definicion.get('media'))}
        if item:
            fila['sid'] = item.get('sid')
            fila.update(_estado_whatsapp(item))
        else:
            fila['estado'] = 'no creada'
        res.append(fila)
    return {'plantillas': res}, 200
