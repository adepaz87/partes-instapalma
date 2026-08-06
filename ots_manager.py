"""
Gestor de OTs (Órdenes de Trabajo) TBSA para Instapalma
Integración con bot actual y flujo de emails
"""

import os
import psycopg2
from datetime import datetime
import json

def get_db():
    return psycopg2.connect(os.environ.get('DATABASE_URL', ''))

def init_ots_table():
    """Crea la tabla de OTs si no existe."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ots (
                id SERIAL PRIMARY KEY,
                numero_ot VARCHAR(50) UNIQUE NOT NULL,
                centro VARCHAR(100),
                averia TEXT,
                prioridad VARCHAR(20) DEFAULT 'normal',
                fecha_recibida VARCHAR(20),
                fecha_limite VARCHAR(20),
                observaciones TEXT,
                estado VARCHAR(20) DEFAULT 'pendiente',
                operario_asignado VARCHAR(100),
                fecha_asignacion TIMESTAMP,
                fecha_resolucion TIMESTAMP,
                respuesta_operario TEXT,
                fotos_urls TEXT,
                origen VARCHAR(20) DEFAULT 'email',
                email_origen VARCHAR(100),
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("Tabla OTs inicializada OK")
    except Exception as e:
        print(f"Error init OTs table: {e}")

def crear_ot_desde_email(numero_ot, centro, averia, fecha_recibida, email_origen, observaciones=''):
    """Crea una OT desde un email recibido."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO ots (numero_ot, centro, averia, fecha_recibida, observaciones, email_origen, origen, estado)
            VALUES (%s, %s, %s, %s, %s, %s, 'email', 'pendiente')
            ON CONFLICT (numero_ot) DO UPDATE 
            SET centro=%s, averia=%s, fecha_recibida=%s, observaciones=%s, email_origen=%s, updated_at=NOW()
            RETURNING id
        """, (numero_ot, centro, averia, fecha_recibida, observaciones, email_origen,
              centro, averia, fecha_recibida, observaciones, email_origen))
        ot_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        print(f"OT creada desde email: {numero_ot} (id={ot_id})")
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
        print(f"OT creada manualmente: {numero_ot} (id={ot_id})")
        return ot_id
    except Exception as e:
        print(f"Error crear OT manual: {e}")
        return None

def obtener_ot(numero_ot):
    """Obtiene los detalles de una OT."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM ots WHERE numero_ot=%s", (numero_ot,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            cols = ['id', 'numero_ot', 'centro', 'averia', 'prioridad', 'fecha_recibida', 
                   'fecha_limite', 'observaciones', 'estado', 'operario_asignado', 
                   'fecha_asignacion', 'fecha_resolucion', 'respuesta_operario', 'fotos_urls', 
                   'origen', 'email_origen', 'created_at', 'updated_at']
            return dict(zip(cols, row))
        return None
    except Exception as e:
        print(f"Error obtener OT: {e}")
        return None

def listar_ots_pendientes():
    """Lista todas las OTs pendientes."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT numero_ot, centro, averia, prioridad, estado, operario_asignado 
            FROM ots WHERE estado='pendiente' ORDER BY created_at DESC
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
        if result:
            print(f"OT {numero_ot} asignada a {operario}")
            return True
        return False
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
        if result:
            print(f"OT {numero_ot} resuelta")
            return True
        return False
    except Exception as e:
        print(f"Error resolver OT: {e}")
        return False

def generar_resumen_ot(numero_ot):
    """Genera un resumen de la OT para WhatsApp y email."""
    ot = obtener_ot(numero_ot)
    if not ot:
        return None
    
    resumen = {
        'numero': ot['numero_ot'],
        'centro': ot['centro'],
        'averia': ot['averia'],
        'prioridad': ot['prioridad'],
        'estado': ot['estado'],
        'operario_asignado': ot['operario_asignado'],
        'respuesta': ot['respuesta_operario'],
        'fotos': ot['fotos_urls']
    }
    return resumen

def formato_wa_ot(numero_ot, incluir_operarios=False, operarios_dict=None):
    """Formatea una OT para mensaje WhatsApp."""
    ot = obtener_ot(numero_ot)
    if not ot:
        return None
    
    centro = ot['centro'] or 'Sin especificar'
    averia = ot['averia'] or 'Sin descripción'
    prioridad = ot['prioridad'] or 'normal'
    estado = ot['estado']
    operario = ot['operario_asignado'] or 'Sin asignar'
    
    txt = f"""🔧 *OT {numero_ot}*
━━━━━━━━━━━━━━━━━━
📍 Centro: {centro}
⚙️ Avería: {averia}
⚡ Prioridad: {prioridad.upper()}
📊 Estado: {estado.upper()}
👤 Asignado a: {operario}"""
    
    if incluir_operarios and operarios_dict:
        txt += "\n\n*Operarios disponibles:*\n"
        for idx, (numero, nombre) in enumerate(operarios_dict.items(), 1):
            txt += f"{idx}. {nombre}\n"
    
    return txt

