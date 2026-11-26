import os
from datetime import datetime, timezone
from decimal import Decimal
from flask import Flask, jsonify, request, send_from_directory
import pymysql
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TALLAS = ["24", "24.5", "25", "25.5", "26", "26.5", "27", "27.5", "28"]

app = Flask(__name__)


def get_db():
    """Create a new PyMySQL connection using environment variables."""
    kwargs = {
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("DB_NAME", "sgcic"),
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": False,
    }
    if os.getenv("DB_SSL", "false").lower() == "true":
        kwargs["ssl"] = {"ssl": {}}
    return pymysql.connect(**kwargs)


def _exec_schema_sql(cur):
    """Execute schema.sql statements one by one."""
    schema_path = os.path.join(BASE_DIR, "schema.sql")
    with open(schema_path, "r", encoding="utf-8") as f:
        raw = f.read()
    lines = [ln for ln in raw.splitlines() if not ln.strip().startswith("--")]
    cleaned = "\n".join(lines)
    for stmt in cleaned.split(";"):
        sql = stmt.strip()
        if sql:
            cur.execute(sql)


def init_db():
    """Verify database tables exist and seed initial data if empty."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE 'usuarios'")
            has_table = cur.fetchone() is not None
            if not has_table:
                _exec_schema_sql(cur)
                conn.commit()
            else:
                cur.execute("SELECT COUNT(*) AS cnt FROM usuarios")
                row = cur.fetchone()
                if not row or row["cnt"] == 0:
                    _exec_schema_sql(cur)
                    conn.commit()
    finally:
        conn.close()


def _dt_to_ms(dt):
    """Convert MySQL DATETIME (UTC/naive) to JS epoch milliseconds."""
    if not dt:
        return None
    if dt.tzinfo is None:
        return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
    return int(dt.timestamp() * 1000)


def _verify_password(stored, provided):
    """Support hashed passwords and legacy seeded plaintext passwords."""
    if stored.startswith(("pbkdf2:", "scrypt:")):
        return check_password_hash(stored, provided)
    return stored == provided


def _comision_por_par(cur):
    cur.execute("SELECT temporada_actual FROM configuracion WHERE id_config = 1")
    row = cur.fetchone()
    return 25.0 if (row and row["temporada_actual"] == "Alta") else 15.0


def _asignar_siguiente_cliente(cur, v_id, v_nombre):
    """Assign the oldest waiting client to vendor v_id if one exists."""
    cur.execute(
        "SELECT id_cliente, hora_llegada FROM clientes_espera "
        "WHERE estado = 'Esperando' ORDER BY id_cliente ASC LIMIT 1"
    )
    cli = cur.fetchone()
    if not cli:
        return None
    cur.execute("SELECT COALESCE(MAX(orden_turno), 0) AS mx FROM usuarios WHERE rol = 'vendedor'")
    mx = cur.fetchone()["mx"]
    now = datetime.utcnow()
    esp_seg = max(0, int((now - cli["hora_llegada"]).total_seconds()))
    hora_str = datetime.now().strftime("%I:%M %p").lstrip("0")
    cur.execute(
        "UPDATE clientes_espera SET estado = 'Atendido', vendedor_asignado = %s, "
        "tiempo_espera_seg = %s, hora_atencion_str = %s WHERE id_cliente = %s",
        (v_nombre, esp_seg, hora_str, cli["id_cliente"]),
    )
    cur.execute(
        "UPDATE usuarios SET estado_piso = 'Atendiendo', cliente_actual = %s, orden_turno = %s "
        "WHERE id_usuario = %s",
        (cli["id_cliente"], mx + 1, v_id),
    )
    return cli["id_cliente"]


@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "34408cc311.html")


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    username = (data.get("u") or "").strip().lower()
    password = data.get("p") or ""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM usuarios WHERE LOWER(username) = %s LIMIT 1", (username,))
            u = cur.fetchone()
            if not u or not _verify_password(u["password_hash"], password):
                return jsonify({"ok": False, "error": "Usuario o contraseña incorrectos. Revisa los datos e inténtalo de nuevo."}), 401
            if not u["activo"]:
                return jsonify({"ok": False, "error": "Esta cuenta se encuentra inactiva. Contacta al administrador."}), 403
            return jsonify({
                "ok": True,
                "user": {
                    "id": u["id_usuario"],
                    "u": u["username"],
                    "rol": u["rol"],
                    "n": u["nombre_completo"],
                    "dni_rfc": u["dni_rfc"],
                    "correo": u["correo"],
                    "activo": bool(u["activo"]),
                },
            })
    finally:
        conn.close()


@app.route("/api/estado", methods=["GET"])
def api_estado():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT temporada_actual, umbral_alerta_seg FROM configuracion WHERE id_config = 1")
            cfg = cur.fetchone() or {"temporada_actual": "Regular", "umbral_alerta_seg": 180}

            cur.execute("SELECT * FROM usuarios ORDER BY orden_turno ASC, id_usuario ASC")
            u_rows = cur.fetchall()
            users = [
                {
                    "id": u["id_usuario"],
                    "u": u["username"],
                    "rol": u["rol"],
                    "n": u["nombre_completo"],
                    "dni_rfc": u["dni_rfc"],
                    "correo": u["correo"],
                    "activo": bool(u["activo"]),
                }
                for u in u_rows
            ]

            cur.execute(
                "SELECT id_vendedor, COALESCE(SUM(cantidad),0) AS pares, "
                "COALESCE(SUM(total_pagado),0) AS monto, COALESCE(SUM(comision),0) AS com "
                "FROM ventas GROUP BY id_vendedor"
            )
            stats = {r["id_vendedor"]: r for r in cur.fetchall()}

            vend = []
            order = []
            for u in u_rows:
                if u["rol"] == "vendedor" and u["activo"]:
                    st = stats.get(u["id_usuario"], {})
                    vend.append({
                        "id": u["id_usuario"],
                        "n": u["nombre_completo"],
                        "st": u["estado_piso"],
                        "cli": u["cliente_actual"],
                        "pares": int(st.get("pares", 0)),
                        "monto": float(st.get("monto", 0)),
                        "com": float(st.get("com", 0)),
                    })
                    order.append(u["id_usuario"])

            cur.execute("SELECT id_cliente, hora_llegada FROM clientes_espera WHERE estado = 'Esperando' ORDER BY id_cliente ASC")
            cola = [{"id": c["id_cliente"], "t0": _dt_to_ms(c["hora_llegada"])} for c in cur.fetchall()]

            cur.execute(
                "SELECT id_cliente, vendedor_asignado, tiempo_espera_seg, hora_atencion_str "
                "FROM clientes_espera WHERE estado = 'Atendido' ORDER BY id_cliente ASC"
            )
            atendidos = [
                {
                    "v": a["vendedor_asignado"],
                    "c": a["id_cliente"],
                    "esp": int(a["tiempo_espera_seg"] or 0),
                    "h": a["hora_atencion_str"] or "",
                }
                for a in cur.fetchall()
            ]

            cur.execute(
                "SELECT * FROM pedidos_bodega WHERE estado IN ('En preparación', 'Listo') ORDER BY id_pedido ASC"
            )
            p_rows = cur.fetchall()
            ped_map = {}
            pedidos = []
            for p in p_rows:
                obj = {
                    "id": p["id_pedido"],
                    "v": p["id_vendedor"],
                    "cli": p["numero_cliente"],
                    "est": p["estado"],
                    "desc": float(p["descuento_global"] or 0),
                    "t0": _dt_to_ms(p["fecha_creacion"]),
                    "tListo": _dt_to_ms(p["fecha_listo"]),
                    "items": [],
                }
                ped_map[p["id_pedido"]] = obj
                pedidos.append(obj)

            if ped_map:
                cur.execute(
                    "SELECT * FROM pedido_items WHERE id_pedido IN %s ORDER BY id_item ASC",
                    (tuple(ped_map.keys()),),
                )
                for it in cur.fetchall():
                    ped_map[it["id_pedido"]]["items"].append({
                        "itemId": it["id_item"],
                        "sku": it["sku"],
                        "mod": it["modelo"],
                        "cat": it["categoria"],
                        "seg": it["segmento"],
                        "t": it["talla"],
                        "cant": int(it["cantidad"]),
                        "pr": float(it["precio_unitario"]),
                        "loc": it["ubicacion"],
                        "est": it["estado"],
                    })

            cur.execute("SELECT * FROM ventas ORDER BY id_venta ASC")
            ventas = [
                {
                    "folio": v["folio"],
                    "fechaISO": v["fecha_iso"].isoformat() if hasattr(v["fecha_iso"], "isoformat") else str(v["fecha_iso"]),
                    "hora": v["hora_str"],
                    "v": v["nombre_vendedor"],
                    "vId": v["id_vendedor"],
                    "sku": v["sku"],
                    "mod": v["modelo"],
                    "cat": v["categoria"],
                    "seg": v["segmento"],
                    "t": v["talla"],
                    "cant": int(v["cantidad"]),
                    "precioUnit": float(v["precio_unitario"]),
                    "subtotal": float(v["subtotal"]),
                    "descuento": float(v["descuento"]),
                    "tot": float(v["total_pagado"]),
                    "c": float(v["comision"]),
                }
                for v in cur.fetchall()
            ]

            cur.execute("SELECT * FROM productos WHERE activo = 1 ORDER BY sku ASC")
            prod_rows = cur.fetchall()
            cur.execute("SELECT sku, talla, existencia FROM inventario_tallas")
            inv_map = {}
            for r in cur.fetchall():
                inv_map.setdefault(r["sku"], {})[r["talla"]] = int(r["existencia"])

            prod = [
                {
                    "sku": p["sku"],
                    "mod": p["modelo"],
                    "cat": p["categoria"],
                    "seg": p["segmento"],
                    "pr": float(p["precio"]),
                    "loc": p["ubicacion"],
                    "st": inv_map.get(p["sku"], {}),
                }
                for p in prod_rows
            ]

            cur.execute("SELECT * FROM proveedores WHERE activo = 1 ORDER BY id_proveedor ASC")
            proveedores = [
                {
                    "id": pr["id_proveedor"],
                    "nombre": pr["nombre"],
                    "contacto": pr["contacto"],
                    "telefono": pr["telefono"],
                    "correo": pr["correo"],
                    "categoria_calzado": pr["categoria_calzado"],
                }
                for pr in cur.fetchall()
            ]

            return jsonify({
                "config": {"temp": cfg["temporada_actual"], "umbral": str(cfg["umbral_alerta_seg"])},
                "users": users,
                "vend": vend,
                "order": order,
                "cola": cola,
                "atendidos": atendidos,
                "pedidos": pedidos,
                "ventas": ventas,
                "prod": prod,
                "proveedores": proveedores,
            })
    finally:
        conn.close()


@app.route("/api/config", methods=["POST"])
def api_config():
    data = request.get_json(silent=True) or {}
    temp = data.get("temp", "Regular")
    umbral = int(data.get("umbral", 180))
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE configuracion SET temporada_actual = %s, umbral_alerta_seg = %s WHERE id_config = 1",
                (temp, umbral),
            )
            conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/checkin", methods=["POST"])
def api_checkin():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_usuario, nombre_completo FROM usuarios "
                "WHERE rol = 'vendedor' AND activo = 1 AND estado_piso = 'Disponible' "
                "ORDER BY orden_turno ASC, id_usuario ASC LIMIT 1 FOR UPDATE"
            )
            v = cur.fetchone()
            if v:
                hora_str = datetime.now().strftime("%I:%M %p").lstrip("0")
                cur.execute(
                    "INSERT INTO clientes_espera (hora_llegada, estado, vendedor_asignado, tiempo_espera_seg, hora_atencion_str) "
                    "VALUES (UTC_TIMESTAMP(), 'Atendido', %s, 0, %s)",
                    (v["nombre_completo"], hora_str),
                )
                cid = cur.lastrowid
                cur.execute("SELECT COALESCE(MAX(orden_turno), 0) AS mx FROM usuarios WHERE rol = 'vendedor'")
                mx = cur.fetchone()["mx"]
                cur.execute(
                    "UPDATE usuarios SET estado_piso = 'Atendiendo', cliente_actual = %s, orden_turno = %s WHERE id_usuario = %s",
                    (cid, mx + 1, v["id_usuario"]),
                )
                conn.commit()
                return jsonify({"ok": True, "msg": f"🎯 ¡Atención asignada! Cliente #{cid} asignado automáticamente a {v['nombre_completo']}."})
            else:
                cur.execute("INSERT INTO clientes_espera (hora_llegada, estado) VALUES (UTC_TIMESTAMP(), 'Esperando')")
                cid = cur.lastrowid
                conn.commit()
                return jsonify({"ok": True, "msg": f"Cliente #{cid} registrado en fila de espera (todos los vendedores están ocupados o en descanso)."})
    finally:
        conn.close()


@app.route("/api/asignar", methods=["POST"])
def api_asignar():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_usuario, nombre_completo FROM usuarios "
                "WHERE rol = 'vendedor' AND activo = 1 AND estado_piso = 'Disponible' "
                "ORDER BY orden_turno ASC, id_usuario ASC LIMIT 1 FOR UPDATE"
            )
            v = cur.fetchone()
            if not v:
                return jsonify({"ok": False, "msg": "Ningún vendedor está disponible. Avisa a gerencia para reforzar piso."}), 400
            cid = _asignar_siguiente_cliente(cur, v["id_usuario"], v["nombre_completo"])
            if not cid:
                return jsonify({"ok": False, "msg": "No hay clientes en fila de espera."}), 400
            conn.commit()
            return jsonify({"ok": True, "msg": f"{v['nombre_completo']} asignado manualmente al cliente {cid}."})
    finally:
        conn.close()


@app.route("/api/vendedores/<int:id>/estado", methods=["POST"])
def api_vendedor_estado(id):
    data = request.get_json(silent=True) or {}
    nuevo_st = data.get("st", "Disponible")
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id_usuario, nombre_completo, estado_piso FROM usuarios WHERE id_usuario = %s FOR UPDATE", (id,))
            v = cur.fetchone()
            if not v:
                return jsonify({"ok": False, "msg": "Vendedor no encontrado."}), 404
            if v["estado_piso"] == "Atendiendo" and nuevo_st != "Atendiendo":
                return jsonify({"ok": False, "msg": "Finaliza la atención antes de cambiar el estado."}), 400
            msg = ""
            if nuevo_st == "Disponible":
                cid = _asignar_siguiente_cliente(cur, id, v["nombre_completo"])
                if cid:
                    msg = f"✅ {v['nombre_completo']} pasó a Disponible y tomó de inmediato al Cliente #{cid} de la fila de espera."
                else:
                    cur.execute("UPDATE usuarios SET estado_piso = 'Disponible' WHERE id_usuario = %s", (id,))
            else:
                cur.execute("UPDATE usuarios SET estado_piso = %s WHERE id_usuario = %s", (nuevo_st, id))
            conn.commit()
            return jsonify({"ok": True, "msg": msg})
    finally:
        conn.close()


@app.route("/api/vendedores/<int:id>/finalizar", methods=["POST"])
def api_vendedor_finalizar(id):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id_usuario, nombre_completo FROM usuarios WHERE id_usuario = %s FOR UPDATE", (id,))
            v = cur.fetchone()
            if not v:
                return jsonify({"ok": False, "msg": "Vendedor no encontrado."}), 404
            cur.execute("UPDATE usuarios SET cliente_actual = NULL WHERE id_usuario = %s", (id,))
            cid = _asignar_siguiente_cliente(cur, id, v["nombre_completo"])
            if cid:
                msg = f"✅ {v['nombre_completo']} finalizó atención y tomó automáticamente al Cliente #{cid} de la fila de espera."
            else:
                cur.execute("UPDATE usuarios SET estado_piso = 'Disponible', cliente_actual = NULL WHERE id_usuario = %s", (id,))
                msg = f"{v['nombre_completo']} quedó disponible para recibir clientes."
            conn.commit()
            return jsonify({"ok": True, "msg": msg})
    finally:
        conn.close()


@app.route("/api/pedidos", methods=["POST"])
def api_pedidos_crear():
    data = request.get_json(silent=True) or {}
    v_id = data.get("vId")
    sku = str(data.get("sku") or "")
    talla = str(data.get("t") or "")
    cant = max(1, int(data.get("cant") or 1))
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id_usuario, cliente_actual FROM usuarios WHERE id_usuario = %s", (v_id,))
            v = cur.fetchone()
            if not v or not v["cliente_actual"]:
                return jsonify({"ok": False, "msg": "Primero ve a Piso de ventas: asigna un cliente o selecciona al vendedor que atiende."}), 400
            if not talla:
                return jsonify({"ok": False, "msg": "Elige una talla antes de crear el pedido."}), 400

            cur.execute("SELECT * FROM productos WHERE sku = %s AND activo = 1", (sku,))
            prod = cur.fetchone()
            if not prod:
                return jsonify({"ok": False, "msg": "Producto no encontrado."}), 404

            cur.execute(
                "SELECT existencia FROM inventario_tallas WHERE sku = %s AND talla = %s FOR UPDATE",
                (sku, talla),
            )
            inv = cur.fetchone()
            disp = int(inv["existencia"]) if inv else 0
            if disp < cant:
                return jsonify({"ok": False, "msg": f"Existencia insuficiente ({disp} disponibles en talla {talla})."}), 400

            cur.execute(
                "UPDATE inventario_tallas SET existencia = existencia - %s WHERE sku = %s AND talla = %s",
                (cant, sku, talla),
            )

            cur.execute(
                "SELECT id_pedido FROM pedidos_bodega "
                "WHERE id_vendedor = %s AND numero_cliente = %s AND estado IN ('En preparación', 'Listo') "
                "ORDER BY id_pedido ASC LIMIT 1 FOR UPDATE",
                (v_id, v["cliente_actual"]),
            )
            ped = cur.fetchone()
            if ped:
                ped_id = ped["id_pedido"]
                cur.execute(
                    "SELECT id_item FROM pedido_items "
                    "WHERE id_pedido = %s AND sku = %s AND talla = %s AND estado = 'En preparación' LIMIT 1",
                    (ped_id, sku, talla),
                )
                ex_item = cur.fetchone()
                if ex_item:
                    cur.execute(
                        "UPDATE pedido_items SET cantidad = cantidad + %s WHERE id_item = %s",
                        (cant, ex_item["id_item"]),
                    )
                else:
                    cur.execute(
                        "INSERT INTO pedido_items (id_pedido, sku, modelo, categoria, segmento, talla, cantidad, precio_unitario, ubicacion, estado) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'En preparación')",
                        (ped_id, sku, prod["modelo"], prod["categoria"], prod["segmento"], talla, cant, prod["precio"], prod["ubicacion"]),
                    )
                cur.execute(
                    "UPDATE pedidos_bodega SET estado = 'En preparación', fecha_listo = NULL WHERE id_pedido = %s",
                    (ped_id,),
                )
            else:
                cur.execute(
                    "INSERT INTO pedidos_bodega (id_vendedor, numero_cliente, estado, descuento_global, fecha_creacion) "
                    "VALUES (%s, %s, 'En preparación', 0.00, UTC_TIMESTAMP())",
                    (v_id, v["cliente_actual"]),
                )
                ped_id = cur.lastrowid
                cur.execute(
                    "INSERT INTO pedido_items (id_pedido, sku, modelo, categoria, segmento, talla, cantidad, precio_unitario, ubicacion, estado) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'En preparación')",
                    (ped_id, sku, prod["modelo"], prod["categoria"], prod["segmento"], talla, cant, prod["precio"], prod["ubicacion"]),
                )

            cur.execute("SELECT COALESCE(SUM(cantidad), 0) AS tot FROM pedido_items WHERE id_pedido = %s", (ped_id,))
            tot_pares = int(cur.fetchone()["tot"])
            conn.commit()
            suf = "es" if tot_pares > 1 else ""
            return jsonify({"ok": True, "msg": f"Calzado añadido al Pedido #{ped_id} ({tot_pares} par{suf} en total)."})
    finally:
        conn.close()


@app.route("/api/pedidos/<int:id>/items/<int:item_id>", methods=["PATCH"])
def api_pedido_item_patch(id, item_id):
    data = request.get_json(silent=True) or {}
    action = data.get("action", "delta")
    delta = int(data.get("delta") or 0)
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM pedido_items WHERE id_item = %s AND id_pedido = %s FOR UPDATE", (item_id, id))
            it = cur.fetchone()
            if not it:
                return jsonify({"ok": False, "msg": "Artículo no encontrado."}), 404

            msg = ""
            if action == "quitar" or (delta < 0 and it["cantidad"] + delta <= 0):
                dev = it["cantidad"]
                cur.execute(
                    "UPDATE inventario_tallas SET existencia = existencia + %s WHERE sku = %s AND talla = %s",
                    (dev, it["sku"], it["talla"]),
                )
                cur.execute("DELETE FROM pedido_items WHERE id_item = %s", (item_id,))
                cur.execute("SELECT COUNT(*) AS cnt FROM pedido_items WHERE id_pedido = %s", (id,))
                if cur.fetchone()["cnt"] == 0:
                    cur.execute("DELETE FROM pedidos_bodega WHERE id_pedido = %s", (id,))
                    msg = f"Se eliminó el artículo y se canceló el Pedido #{id}."
                else:
                    cur.execute(
                        "SELECT COUNT(*) AS prep FROM pedido_items WHERE id_pedido = %s AND estado = 'En preparación'",
                        (id,),
                    )
                    if cur.fetchone()["prep"] == 0:
                        cur.execute(
                            "UPDATE pedidos_bodega SET estado = 'Listo', fecha_listo = COALESCE(fecha_listo, UTC_TIMESTAMP()) WHERE id_pedido = %s",
                            (id,),
                        )
                    if action == "quitar":
                        msg = f"Se quitó {it['modelo']} (talla {it['talla']}) del Pedido #{id} y se devolvió al stock."
            elif delta < 0:
                dev = min(it["cantidad"], abs(delta))
                cur.execute(
                    "UPDATE inventario_tallas SET existencia = existencia + %s WHERE sku = %s AND talla = %s",
                    (dev, it["sku"], it["talla"]),
                )
                cur.execute("UPDATE pedido_items SET cantidad = cantidad - %s WHERE id_item = %s", (dev, item_id))
            elif delta > 0:
                cur.execute(
                    "SELECT existencia FROM inventario_tallas WHERE sku = %s AND talla = %s FOR UPDATE",
                    (it["sku"], it["talla"]),
                )
                inv = cur.fetchone()
                disp = int(inv["existencia"]) if inv else 0
                if disp < delta:
                    return jsonify({"ok": False, "msg": f"Existencia insuficiente ({disp} disponibles en talla {it['talla']})."}), 400
                cur.execute(
                    "UPDATE inventario_tallas SET existencia = existencia - %s WHERE sku = %s AND talla = %s",
                    (delta, it["sku"], it["talla"]),
                )
                cur.execute("UPDATE pedido_items SET cantidad = cantidad + %s WHERE id_item = %s", (delta, item_id))

            conn.commit()
            return jsonify({"ok": True, "msg": msg})
    finally:
        conn.close()


@app.route("/api/pedidos/<int:id>/descuento", methods=["PATCH"])
def api_pedido_descuento(id):
    data = request.get_json(silent=True) or {}
    desc = max(0.0, float(data.get("desc") or 0))
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(SUM(cantidad * precio_unitario), 0) AS sub FROM pedido_items WHERE id_pedido = %s", (id,))
            sub = float(cur.fetchone()["sub"] or 0)
            if desc > sub:
                desc = sub
            cur.execute("UPDATE pedidos_bodega SET descuento_global = %s WHERE id_pedido = %s", (desc, id))
            conn.commit()
        return jsonify({"ok": True, "desc": desc})
    finally:
        conn.close()


@app.route("/api/pedidos/<int:id>/items/<int:item_id>/listo", methods=["POST"])
def api_pedido_item_listo(id, item_id):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE pedido_items SET estado = 'Listo' WHERE id_item = %s AND id_pedido = %s", (item_id, id))
            cur.execute("SELECT COUNT(*) AS prep FROM pedido_items WHERE id_pedido = %s AND estado = 'En preparación'", (id,))
            if cur.fetchone()["prep"] == 0:
                cur.execute(
                    "UPDATE pedidos_bodega SET estado = 'Listo', fecha_listo = UTC_TIMESTAMP() WHERE id_pedido = %s",
                    (id,),
                )
            conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/pedidos/<int:id>/listo", methods=["POST"])
def api_pedido_todo_listo(id):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE pedido_items SET estado = 'Listo' WHERE id_pedido = %s", (id,))
            cur.execute("UPDATE pedidos_bodega SET estado = 'Listo', fecha_listo = UTC_TIMESTAMP() WHERE id_pedido = %s", (id,))
            conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/pedidos/<int:id>/cancelar", methods=["POST"])
def api_pedido_cancelar(id):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT sku, talla, cantidad FROM pedido_items WHERE id_pedido = %s FOR UPDATE", (id,))
            items = cur.fetchall()
            tot_dev = 0
            for it in items:
                c = int(it["cantidad"])
                cur.execute(
                    "UPDATE inventario_tallas SET existencia = existencia + %s WHERE sku = %s AND talla = %s",
                    (c, it["sku"], it["talla"]),
                )
                tot_dev += c
            cur.execute("DELETE FROM pedidos_bodega WHERE id_pedido = %s", (id,))
            conn.commit()
            return jsonify({"ok": True, "msg": f"Pedido #{id} cancelado. Se devolvieron {tot_dev} par(es) al stock disponible."})
    finally:
        conn.close()


@app.route("/api/pedidos/<int:id>/vender", methods=["POST"])
def api_pedido_vender(id):
    data = request.get_json(silent=True) or {}
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM pedidos_bodega WHERE id_pedido = %s FOR UPDATE", (id,))
            ped = cur.fetchone()
            if not ped or ped["estado"] in ("Entregado", "Cancelado"):
                return jsonify({"ok": False, "msg": "Pedido no disponible para venta."}), 400

            cur.execute("SELECT * FROM pedido_items WHERE id_pedido = %s ORDER BY id_item ASC", (id,))
            items = cur.fetchall()
            if not items:
                return jsonify({"ok": False, "msg": "El pedido no contiene artículos."}), 400

            cur.execute("SELECT nombre_completo FROM usuarios WHERE id_usuario = %s", (ped["id_vendedor"],))
            v_row = cur.fetchone()
            v_nombre = v_row["nombre_completo"] if v_row else "Vendedor"

            total_sub = sum(float(it["cantidad"]) * float(it["precio_unitario"]) for it in items)
            desc = float(data["desc"]) if "desc" in data and data["desc"] is not None else float(ped["descuento_global"] or 0)
            desc = max(0.0, min(total_sub, desc))

            now = datetime.now()
            cur.execute("SELECT COUNT(*) AS cnt FROM ventas")
            seq = int(cur.fetchone()["cnt"]) + 1
            folio = f"TK-{now.strftime('%y%m%d')}-{seq:04d}"
            fecha_iso = now.strftime("%Y-%m-%d")
            hora_str = now.strftime("%I:%M %p").lstrip("0")

            com_par = _comision_por_par(cur)
            desc_rest = round(desc, 2)
            tot_pares = 0

            for idx, it in enumerate(items):
                cant = int(it["cantidad"])
                pr_unit = float(it["precio_unitario"])
                sub = round(cant * pr_unit, 2)
                item_desc = 0.0
                if desc > 0 and total_sub > 0:
                    if idx == len(items) - 1:
                        item_desc = min(sub, round(desc_rest, 2))
                    else:
                        item_desc = min(sub, round(desc * (sub / total_sub), 2))
                        desc_rest = round(desc_rest - item_desc, 2)
                item_tot = round(sub - item_desc, 2)
                item_com = round(com_par * cant, 2)
                tot_pares += cant

                cur.execute(
                    "INSERT INTO ventas (folio, fecha_iso, hora_str, id_vendedor, nombre_vendedor, "
                    "sku, modelo, categoria, segmento, talla, cantidad, precio_unitario, subtotal, descuento, total_pagado, comision) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        folio, fecha_iso, hora_str, ped["id_vendedor"], v_nombre,
                        it["sku"], it["modelo"], it["categoria"], it["segmento"], it["talla"],
                        cant, pr_unit, sub, item_desc, item_tot, item_com,
                    ),
                )

            cur.execute("UPDATE pedidos_bodega SET estado = 'Entregado' WHERE id_pedido = %s", (id,))
            conn.commit()
            suf = "es" if tot_pares > 1 else ""
            return jsonify({
                "ok": True,
                "folio": folio,
                "msg": f"Venta registrada a nombre de {v_nombre} ({tot_pares} par{suf}). Folio: {folio}",
            })
    finally:
        conn.close()


@app.route("/api/inventario/agregar", methods=["POST"])
def api_inventario_agregar():
    data = request.get_json(silent=True) or {}
    sku = str(data.get("sku") or "")
    talla = str(data.get("t") or "")
    cant = int(data.get("cant") or 0)
    if cant <= 0:
        return jsonify({"ok": False, "msg": "Cantidad inválida."}), 400
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT modelo FROM productos WHERE sku = %s", (sku,))
            p = cur.fetchone()
            if not p:
                return jsonify({"ok": False, "msg": "Producto no encontrado."}), 404
            cur.execute(
                "INSERT INTO inventario_tallas (sku, talla, existencia) VALUES (%s, %s, %s) "
                "ON DUPLICATE KEY UPDATE existencia = existencia + VALUES(existencia)",
                (sku, talla, cant),
            )
            cur.execute("SELECT existencia FROM inventario_tallas WHERE sku = %s AND talla = %s", (sku, talla))
            nuevo = int(cur.fetchone()["existencia"])
            conn.commit()
            return jsonify({
                "ok": True,
                "msg": f"Se agregaron +{cant} pares al modelo {p['modelo']} en talla {talla}. Existencia actual: {nuevo}",
            })
    finally:
        conn.close()


@app.route("/api/productos", methods=["POST"])
def api_productos_crear():
    data = request.get_json(silent=True) or {}
    sku = (data.get("sku") or "").strip()
    mod = (data.get("mod") or "").strip()
    cat = data.get("cat", "Casual")
    seg = data.get("seg", "Hombre")
    pr = float(data.get("pr") or 0)
    loc = (data.get("loc") or "Pasillo A · Estante 01 · Sección 1").strip()
    st_base = max(0, int(data.get("stBase") if data.get("stBase") is not None else 3))
    if not sku or not mod or pr <= 0:
        return jsonify({"ok": False, "msg": "Por favor completa SKU, nombre de modelo y precio válido."}), 400
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT sku FROM productos WHERE sku = %s", (sku,))
            if cur.fetchone():
                return jsonify({"ok": False, "msg": f"El SKU {sku} ya existe en el inventario."}), 400
            cur.execute(
                "INSERT INTO productos (sku, modelo, categoria, segmento, precio, ubicacion, activo) "
                "VALUES (%s, %s, %s, %s, %s, %s, 1)",
                (sku, mod, cat, seg, pr, loc),
            )
            for t in TALLAS:
                cur.execute(
                    "INSERT INTO inventario_tallas (sku, talla, existencia) VALUES (%s, %s, %s)",
                    (sku, t, st_base),
                )
            conn.commit()
            return jsonify({"ok": True, "msg": f"Nuevo modelo registrado: {mod} ({sku}) con segmento {seg}."})
    finally:
        conn.close()


@app.route("/api/productos/<sku>", methods=["PUT"])
def api_productos_editar(sku):
    data = request.get_json(silent=True) or {}
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE productos SET modelo = %s, categoria = %s, segmento = %s, precio = %s, ubicacion = %s "
                "WHERE sku = %s",
                (data.get("mod"), data.get("cat"), data.get("seg"), float(data.get("pr") or 0), data.get("loc"), sku),
            )
            conn.commit()
            return jsonify({"ok": True, "msg": f"Producto SKU {sku} actualizado exitosamente."})
    finally:
        conn.close()


@app.route("/api/productos/<sku>", methods=["DELETE"])
def api_productos_eliminar(sku):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT modelo FROM productos WHERE sku = %s", (sku,))
            p = cur.fetchone()
            cur.execute("UPDATE productos SET activo = 0 WHERE sku = %s", (sku,))
            conn.commit()
            mod = p["modelo"] if p else sku
            return jsonify({"ok": True, "msg": f"Calzado {mod} eliminado del catálogo."})
    finally:
        conn.close()


@app.route("/api/usuarios", methods=["POST"])
def api_usuarios_crear():
    data = request.get_json(silent=True) or {}
    nom = (data.get("n") or "").strip()
    usr = (data.get("u") or "").strip().lower()
    pwd = data.get("p") or ""
    rfc = (data.get("dni_rfc") or "").strip() or f"RFC-{usr.upper()}"
    cor = (data.get("correo") or "").strip() or f"{usr}@3hermanos.mx"
    rol = data.get("rol", "vendedor")
    if not nom or not usr or not pwd:
        return jsonify({"ok": False, "msg": "Completa al menos nombre, usuario y contraseña."}), 400
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id_usuario FROM usuarios WHERE LOWER(username) = %s", (usr,))
            if cur.fetchone():
                return jsonify({"ok": False, "msg": f"El nombre de usuario '{usr}' ya está en uso."}), 400
            cur.execute("SELECT COALESCE(MAX(orden_turno), 0) AS mx FROM usuarios WHERE rol = 'vendedor'")
            mx = int(cur.fetchone()["mx"])
            cur.execute(
                "INSERT INTO usuarios (username, password_hash, nombre_completo, rol, dni_rfc, correo, activo, estado_piso, orden_turno) "
                "VALUES (%s, %s, %s, %s, %s, %s, 1, 'Disponible', %s)",
                (usr, generate_password_hash(pwd), nom, rol, rfc, cor, mx + 1 if rol == "vendedor" else 0),
            )
            conn.commit()
            return jsonify({"ok": True, "msg": f"Empleado {nom} registrado exitosamente como {rol}."})
    finally:
        conn.close()


@app.route("/api/usuarios/<int:id>", methods=["PUT"])
def api_usuarios_editar(id):
    data = request.get_json(silent=True) or {}
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT username FROM usuarios WHERE id_usuario = %s", (id,))
            u = cur.fetchone()
            if not u:
                return jsonify({"ok": False, "msg": "Usuario no encontrado."}), 404
            new_pwd = data.get("p")
            if new_pwd:
                cur.execute(
                    "UPDATE usuarios SET nombre_completo=%s, dni_rfc=%s, correo=%s, rol=%s, password_hash=%s WHERE id_usuario=%s",
                    (data.get("n"), data.get("dni_rfc"), data.get("correo"), data.get("rol"), generate_password_hash(new_pwd), id),
                )
            else:
                cur.execute(
                    "UPDATE usuarios SET nombre_completo=%s, dni_rfc=%s, correo=%s, rol=%s WHERE id_usuario=%s",
                    (data.get("n"), data.get("dni_rfc"), data.get("correo"), data.get("rol"), id),
                )
            conn.commit()
            return jsonify({"ok": True, "msg": f"Datos del usuario {u['username']} actualizados."})
    finally:
        conn.close()


@app.route("/api/usuarios/<int:id>/toggle", methods=["PATCH"])
def api_usuarios_toggle(id):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT username, activo FROM usuarios WHERE id_usuario = %s", (id,))
            u = cur.fetchone()
            if not u:
                return jsonify({"ok": False, "msg": "Usuario no encontrado."}), 404
            nuevo = 0 if u["activo"] else 1
            cur.execute("UPDATE usuarios SET activo = %s WHERE id_usuario = %s", (nuevo, id))
            conn.commit()
            est_txt = "Activo" if nuevo else "Inactivo"
            return jsonify({"ok": True, "msg": f"Usuario {u['username']} marcado como {est_txt}."})
    finally:
        conn.close()


@app.route("/api/proveedores", methods=["POST"])
def api_proveedores_crear():
    data = request.get_json(silent=True) or {}
    nom = (data.get("nombre") or "").strip()
    con = (data.get("contacto") or "").strip()
    if not nom or not con:
        return jsonify({"ok": False, "msg": "Indica al menos el nombre de la empresa proveedora y el contacto."}), 400
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO proveedores (nombre, contacto, telefono, correo, categoria_calzado, activo) "
                "VALUES (%s, %s, %s, %s, %s, 1)",
                (nom, con, data.get("telefono") or "N/D", data.get("correo") or "N/D", data.get("categoria_calzado") or "General"),
            )
            conn.commit()
            return jsonify({"ok": True, "msg": f"Proveedor '{nom}' agregado correctamente."})
    finally:
        conn.close()


@app.route("/api/proveedores/<int:id>", methods=["DELETE"])
def api_proveedores_eliminar(id):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE proveedores SET activo = 0 WHERE id_proveedor = %s", (id,))
            conn.commit()
            return jsonify({"ok": True, "msg": "Proveedor eliminado."})
    finally:
        conn.close()


@app.route("/api/demo/reset", methods=["POST"])
def api_demo_reset():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS = 0")
            for tbl in ("pedido_items", "pedidos_bodega", "ventas", "clientes_espera", "inventario_tallas", "productos", "proveedores", "usuarios", "configuracion"):
                cur.execute(f"TRUNCATE TABLE {tbl}")
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")
            _exec_schema_sql(cur)
            conn.commit()
            return jsonify({"ok": True, "msg": "Sistema restablecido con datos demo iniciales."})
    finally:
        conn.close()


try:
    init_db()
except Exception as exc:
    print(f"[SGCIC] Aviso en init_db al arrancar: {exc}")

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
