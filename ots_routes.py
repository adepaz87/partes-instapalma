"""
Rutas web para gestión de OTs
Se importan en app.py como blueprints
"""

from flask import Blueprint, render_template_string, request, jsonify
import os
from ots_handlers import crear_ot_manual, listar_ots_pendientes, obtener_ot
from datetime import datetime

ots_bp = Blueprint('ots', __name__, url_prefix='/ots')

HTML_FORMULARIO = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Crear OT — Instapalma</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .container {
            background: white;
            border-radius: 12px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            max-width: 500px;
            width: 100%;
            padding: 40px;
        }
        .header {
            text-align: center;
            margin-bottom: 30px;
        }
        .header h1 {
            color: #1a3a5c;
            font-size: 28px;
            margin-bottom: 8px;
        }
        .header p {
            color: #666;
            font-size: 14px;
        }
        .form-group {
            margin-bottom: 20px;
        }
        label {
            display: block;
            margin-bottom: 6px;
            color: #333;
            font-weight: 600;
            font-size: 14px;
        }
        input, select, textarea {
            width: 100%;
            padding: 10px 12px;
            border: 2px solid #e0e0e0;
            border-radius: 6px;
            font-size: 14px;
            font-family: inherit;
            transition: border-color 0.3s;
        }
        input:focus, select:focus, textarea:focus {
            outline: none;
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102,126,234,0.1);
        }
        textarea {
            resize: vertical;
            min-height: 80px;
        }
        .form-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 15px;
        }
        button {
            width: 100%;
            padding: 12px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 6px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
            margin-top: 10px;
        }
        button:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 20px rgba(102,126,234,0.4);
        }
        button:active {
            transform: translateY(0);
        }
        .success-message {
            background: #d4edda;
            color: #155724;
            padding: 12px;
            border-radius: 6px;
            margin-bottom: 20px;
            display: none;
        }
        .error-message {
            background: #f8d7da;
            color: #721c24;
            padding: 12px;
            border-radius: 6px;
            margin-bottom: 20px;
            display: none;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🔧 Nueva OT</h1>
            <p>TBSA — Instapalma Obras y Servicios SLU</p>
        </div>
        
        <div class="success-message" id="successMsg">✅ OT creada exitosamente. Número: <strong id="otNumber"></strong></div>
        <div class="error-message" id="errorMsg"></div>
        
        <form id="otForm">
            <div class="form-group">
                <label for="numero_ot">Número OT *</label>
                <input type="text" id="numero_ot" name="numero_ot" required placeholder="Ej: OT-2026-001">
            </div>
            
            <div class="form-group">
                <label for="centro">Centro *</label>
                <select id="centro" name="centro" required>
                    <option value="">Selecciona un centro...</option>
                    <option value="Spar Mederos">Spar Mederos</option>
                    <option value="Spar El Paso">Spar El Paso</option>
                    <option value="Spar Triana">Spar Triana</option>
                    <option value="Central de Servicios">Central de Servicios</option>
                    <option value="Grupo Móvil">Grupo Móvil</option>
                    <option value="Otros">Otros</option>
                </select>
            </div>
            
            <div class="form-group">
                <label for="averia">Descripción Avería *</label>
                <textarea id="averia" name="averia" required placeholder="Describe el problema..."></textarea>
            </div>
            
            <div class="form-row">
                <div class="form-group">
                    <label for="prioridad">Prioridad *</label>
                    <select id="prioridad" name="prioridad" required>
                        <option value="normal">Normal</option>
                        <option value="alta">Alta</option>
                        <option value="urgente">Urgente</option>
                    </select>
                </div>
                <div class="form-group">
                    <label for="fecha_recibida">Fecha Recibida *</label>
                    <input type="date" id="fecha_recibida" name="fecha_recibida" required>
                </div>
            </div>
            
            <div class="form-group">
                <label for="fecha_limite">Fecha Límite</label>
                <input type="date" id="fecha_limite" name="fecha_limite">
            </div>
            
            <div class="form-group">
                <label for="observaciones">Observaciones</label>
                <textarea id="observaciones" name="observaciones" placeholder="Notas adicionales..."></textarea>
            </div>
            
            <button type="submit">Crear OT</button>
        </form>
    </div>
    
    <script>
        document.getElementById('otForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const datos = {
                numero_ot: document.getElementById('numero_ot').value,
                centro: document.getElementById('centro').value,
                averia: document.getElementById('averia').value,
                prioridad: document.getElementById('prioridad').value,
                fecha_recibida: document.getElementById('fecha_recibida').value,
                fecha_limite: document.getElementById('fecha_limite').value || null,
                observaciones: document.getElementById('observaciones').value
            };
            
            try {
                const response = await fetch('/ots/create', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(datos)
                });
                
                const result = await response.json();
                
                if (result.success) {
                    document.getElementById('otNumber').textContent = datos.numero_ot;
                    document.getElementById('successMsg').style.display = 'block';
                    document.getElementById('otForm').reset();
                    // Ocultar mensaje después de 5 segundos
                    setTimeout(() => {
                        document.getElementById('successMsg').style.display = 'none';
                    }, 5000);
                } else {
                    document.getElementById('errorMsg').textContent = result.error || 'Error desconocido';
                    document.getElementById('errorMsg').style.display = 'block';
                }
            } catch (error) {
                document.getElementById('errorMsg').textContent = 'Error de conexión: ' + error.message;
                document.getElementById('errorMsg').style.display = 'block';
            }
        });
        
        // Establecer fecha actual como mínimo
        document.getElementById('fecha_recibida').valueAsDate = new Date();
    </script>
</body>
</html>
"""

HTML_LISTADO = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OTs — Instapalma</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f5f5f5;
            padding: 20px;
        }
        .container {
            max-width: 1000px;
            margin: 0 auto;
        }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            border-radius: 12px;
            margin-bottom: 30px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .header h1 {
            font-size: 28px;
        }
        .btn-new {
            background: white;
            color: #667eea;
            padding: 10px 20px;
            border-radius: 6px;
            text-decoration: none;
            font-weight: 600;
            cursor: pointer;
        }
        .btn-new:hover {
            transform: scale(1.05);
        }
        .ot-card {
            background: white;
            border-left: 4px solid #667eea;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 15px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }
        .ot-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
        }
        .ot-numero {
            font-size: 18px;
            font-weight: 700;
            color: #1a3a5c;
        }
        .ot-estado {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
        }
        .estado-pendiente { background: #fff3cd; color: #856404; }
        .estado-asignada { background: #d1ecf1; color: #0c5460; }
        .estado-resuelta { background: #d4edda; color: #155724; }
        
        .ot-body {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 15px;
            font-size: 14px;
        }
        .ot-field {
            display: flex;
            flex-direction: column;
        }
        .ot-field-label {
            color: #666;
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            margin-bottom: 4px;
        }
        .ot-field-value {
            color: #333;
            font-weight: 500;
        }
        .no-ots {
            text-align: center;
            padding: 40px;
            color: #999;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📋 OTs — Gestión</h1>
            <a href="/ots/new" class="btn-new">+ Crear OT</a>
        </div>
        
        <div id="otsList"></div>
    </div>
    
    <script>
        async function cargarOTs() {
            try {
                const response = await fetch('/ots/api/list');
                const data = await response.json();
                
                const container = document.getElementById('otsList');
                
                if (!data.ots || data.ots.length === 0) {
                    container.innerHTML = '<div class="no-ots">No hay OTs activas</div>';
                    return;
                }
                
                let html = '';
                data.ots.forEach(ot => {
                    const estadoClass = `estado-${ot.estado}`;
                    html += `
                        <div class="ot-card">
                            <div class="ot-header">
                                <div class="ot-numero">OT ${ot.numero_ot}</div>
                                <span class="ot-estado ${estadoClass}">${ot.estado.toUpperCase()}</span>
                            </div>
                            <div class="ot-body">
                                <div class="ot-field">
                                    <div class="ot-field-label">Centro</div>
                                    <div class="ot-field-value">${ot.centro || 'N/A'}</div>
                                </div>
                                <div class="ot-field">
                                    <div class="ot-field-label">Prioridad</div>
                                    <div class="ot-field-value">${ot.prioridad || 'Normal'}</div>
                                </div>
                                <div class="ot-field">
                                    <div class="ot-field-label">Avería</div>
                                    <div class="ot-field-value">${ot.averia}</div>
                                </div>
                                <div class="ot-field">
                                    <div class="ot-field-label">Asignado a</div>
                                    <div class="ot-field-value">${ot.operario_asignado || 'Sin asignar'}</div>
                                </div>
                            </div>
                        </div>
                    `;
                });
                
                container.innerHTML = html;
            } catch (error) {
                document.getElementById('otsList').innerHTML = '<div class="no-ots">Error cargando OTs</div>';
            }
        }
        
        cargarOTs();
        // Recargar cada 10 segundos
        setInterval(cargarOTs, 10000);
    </script>
</body>
</html>
"""

@ots_bp.route('/new', methods=['GET'])
def formulario_nuevo():
    """Muestra formulario para crear OT"""
    return render_template_string(HTML_FORMULARIO)

@ots_bp.route('', methods=['GET'])
@ots_bp.route('/list', methods=['GET'])
def listado_ots():
    """Muestra listado de OTs activas"""
    return render_template_string(HTML_LISTADO)

@ots_bp.route('/create', methods=['POST'])
def crear_ot_endpoint():
    """Endpoint para crear OT desde formulario"""
    try:
        datos = request.get_json()
        
        if not datos.get('numero_ot') or not datos.get('centro') or not datos.get('averia'):
            return jsonify({'success': False, 'error': 'Faltan campos requeridos'}), 400
        
        # Convertir fechas
        fecha_rec = datos.get('fecha_recibida', datetime.now().strftime('%Y-%m-%d'))
        fecha_lim = datos.get('fecha_limite')
        
        # Formato DD/MM/YYYY
        try:
            from datetime import datetime as dt
            fecha_rec_fmt = dt.strptime(fecha_rec, '%Y-%m-%d').strftime('%d/%m/%Y')
            fecha_lim_fmt = dt.strptime(fecha_lim, '%Y-%m-%d').strftime('%d/%m/%Y') if fecha_lim else ''
        except:
            fecha_rec_fmt = fecha_rec
            fecha_lim_fmt = fecha_lim or ''
        
        ot_id = crear_ot_manual(
            datos['numero_ot'],
            datos['centro'],
            datos['averia'],
            datos.get('prioridad', 'normal'),
            fecha_rec_fmt,
            fecha_lim_fmt,
            datos.get('observaciones', '')
        )
        
        if ot_id:
            return jsonify({'success': True, 'ot_id': ot_id}), 201
        else:
            return jsonify({'success': False, 'error': 'Error al crear OT'}), 500
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@ots_bp.route('/api/list', methods=['GET'])
def api_listar_ots():
    """API para obtener lista de OTs en JSON"""
    try:
        ots = listar_ots_pendientes()
        datos_ots = []
        for numero_ot, centro, averia, prioridad, estado, operario_asignado in ots:
            datos_ots.append({
                'numero_ot': numero_ot,
                'centro': centro,
                'averia': averia,
                'prioridad': prioridad,
                'estado': estado,
                'operario_asignado': operario_asignado
            })
        return jsonify({'ots': datos_ots}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@ots_bp.route('/api/<numero_ot>', methods=['GET'])
def api_obtener_ot(numero_ot):
    """API para obtener detalles de una OT"""
    try:
        ot = obtener_ot(numero_ot)
        if ot:
            return jsonify(ot), 200
        else:
            return jsonify({'error': 'OT no encontrada'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

