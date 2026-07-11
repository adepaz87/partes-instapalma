BACKUP CHATBOT INSTAPALMA — 11/07/2026
========================================

ARCHIVOS:
- app_railway.py  → Código completo del bot (4719 líneas)
- carga_almacen.json   → Inventario almacén (79 artículos)
- carga_personal.json  → Herramienta personal + EPIs (207 registros, 16 operarios)

ESTADO DEL BOT:
- Plataforma: Railway (https://bot-production-66b8.up.railway.app)
- Repo GitHub: adepaz87/partes-instapalma
- BD: PostgreSQL en Railway

MÓDULOS ACTIVOS:
1. Partes de trabajo (con PDF + email + WhatsApp grupo)
2. Salida de almacén / Devolución
3. Consulta: Stock Almacén (buscar/PDF) + Stock Herramienta (PDF)
4. Herramienta: Alta obra / Devolución / Listados (almacén, obra, personal)
5. Vacaciones
6. Resumen fin de mes
7. Vehículos
8. Menú principal con "hola"

TABLAS BD:
- partes
- stock_materiales
- herramienta (79 artículos almacén)
- herramienta_obra (19 registros en obra)
- herramienta_personal (207 registros)
- herramienta_epis
- vacaciones
- estados (gestión de conversaciones)
