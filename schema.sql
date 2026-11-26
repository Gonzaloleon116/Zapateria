-- Esquema MySQL 8 / TiDB Cloud para SGCIC - Zapatería 3 Hermanos
-- Compatible con pedidos consolidados multi-zapato y sincronización multi-equipo

CREATE TABLE IF NOT EXISTS configuracion (
  id_config INT PRIMARY KEY,
  temporada_actual VARCHAR(20) NOT NULL DEFAULT 'Regular',
  umbral_alerta_seg INT NOT NULL DEFAULT 180
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS usuarios (
  id_usuario INT PRIMARY KEY AUTO_INCREMENT,
  username VARCHAR(50) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  nombre_completo VARCHAR(100) NOT NULL,
  rol ENUM('admin','vendedor') NOT NULL DEFAULT 'vendedor',
  dni_rfc VARCHAR(40) NOT NULL DEFAULT '',
  correo VARCHAR(120) NOT NULL DEFAULT '',
  activo TINYINT(1) NOT NULL DEFAULT 1,
  estado_piso ENUM('Disponible','Atendiendo','Descanso') NOT NULL DEFAULT 'Disponible',
  cliente_actual INT NULL,
  orden_turno INT NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS productos (
  sku VARCHAR(20) PRIMARY KEY,
  modelo VARCHAR(150) NOT NULL,
  categoria VARCHAR(50) NOT NULL,
  segmento ENUM('Hombre','Mujer','Niño') NOT NULL DEFAULT 'Hombre',
  precio DECIMAL(10,2) NOT NULL,
  ubicacion VARCHAR(150) NOT NULL,
  activo TINYINT(1) NOT NULL DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS inventario_tallas (
  sku VARCHAR(20) NOT NULL,
  talla VARCHAR(10) NOT NULL,
  existencia INT NOT NULL DEFAULT 0,
  PRIMARY KEY (sku, talla),
  CONSTRAINT fk_inv_producto FOREIGN KEY (sku) REFERENCES productos(sku) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS clientes_espera (
  id_cliente INT PRIMARY KEY AUTO_INCREMENT,
  hora_llegada DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  estado ENUM('Esperando','Atendido') NOT NULL DEFAULT 'Esperando',
  vendedor_asignado VARCHAR(100) NULL,
  tiempo_espera_seg INT NOT NULL DEFAULT 0,
  hora_atencion_str VARCHAR(15) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS pedidos_bodega (
  id_pedido INT PRIMARY KEY AUTO_INCREMENT,
  id_vendedor INT NOT NULL,
  numero_cliente INT NULL,
  estado ENUM('En preparación','Listo','Entregado','Cancelado') NOT NULL DEFAULT 'En preparación',
  descuento_global DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  fecha_listo DATETIME NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS pedido_items (
  id_item INT PRIMARY KEY AUTO_INCREMENT,
  id_pedido INT NOT NULL,
  sku VARCHAR(20) NOT NULL,
  modelo VARCHAR(150) NOT NULL,
  categoria VARCHAR(50) NOT NULL,
  segmento ENUM('Hombre','Mujer','Niño') NOT NULL DEFAULT 'Hombre',
  talla VARCHAR(10) NOT NULL,
  cantidad INT NOT NULL DEFAULT 1,
  precio_unitario DECIMAL(10,2) NOT NULL,
  ubicacion VARCHAR(150) NOT NULL,
  estado ENUM('En preparación','Listo') NOT NULL DEFAULT 'En preparación',
  CONSTRAINT fk_item_pedido FOREIGN KEY (id_pedido) REFERENCES pedidos_bodega(id_pedido) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS ventas (
  id_venta INT PRIMARY KEY AUTO_INCREMENT,
  folio VARCHAR(30) NOT NULL,
  fecha_iso DATE NOT NULL,
  hora_str VARCHAR(10) NOT NULL,
  id_vendedor INT NOT NULL,
  nombre_vendedor VARCHAR(100) NOT NULL,
  sku VARCHAR(20) NOT NULL,
  modelo VARCHAR(150) NOT NULL,
  categoria VARCHAR(50) NOT NULL,
  segmento VARCHAR(20) NOT NULL DEFAULT 'Hombre',
  talla VARCHAR(10) NOT NULL,
  cantidad INT NOT NULL DEFAULT 1,
  precio_unitario DECIMAL(10,2) NOT NULL,
  subtotal DECIMAL(10,2) NOT NULL,
  descuento DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  total_pagado DECIMAL(10,2) NOT NULL,
  comision DECIMAL(10,2) NOT NULL,
  INDEX idx_ventas_folio (folio),
  INDEX idx_ventas_fecha (fecha_iso)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS proveedores (
  id_proveedor INT PRIMARY KEY AUTO_INCREMENT,
  nombre VARCHAR(150) NOT NULL,
  contacto VARCHAR(100) NOT NULL,
  telefono VARCHAR(40) NOT NULL DEFAULT '',
  correo VARCHAR(120) NOT NULL DEFAULT '',
  categoria_calzado VARCHAR(100) NOT NULL DEFAULT 'General',
  activo TINYINT(1) NOT NULL DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Datos iniciales por defecto (idempotentes con INSERT IGNORE)
INSERT IGNORE INTO configuracion (id_config, temporada_actual, umbral_alerta_seg)
VALUES (1, 'Regular', 180);

INSERT IGNORE INTO usuarios (id_usuario, username, password_hash, nombre_completo, rol, dni_rfc, correo, activo, estado_piso, cliente_actual, orden_turno) VALUES
(1, 'admin', 'admin123', 'Administrador', 'admin', 'ADM-850101-HA1', 'admin@zapateria3h.mx', 1, 'Disponible', NULL, 0),
(2, 'vendedor1', '1234', 'Vendedor 01', 'vendedor', 'VEN-950112-MX1', 'vendedor1@zapateria3h.mx', 1, 'Disponible', NULL, 1),
(3, 'vendedor2', '1234', 'Vendedor 02', 'vendedor', 'VEN-950212-MX2', 'vendedor2@zapateria3h.mx', 1, 'Disponible', NULL, 2),
(4, 'vendedor3', '1234', 'Vendedor 03', 'vendedor', 'VEN-950312-MX3', 'vendedor3@zapateria3h.mx', 1, 'Disponible', NULL, 3),
(5, 'vendedor4', '1234', 'Vendedor 04', 'vendedor', 'VEN-950412-MX4', 'vendedor4@zapateria3h.mx', 1, 'Disponible', NULL, 4),
(6, 'vendedor5', '1234', 'Vendedor 05', 'vendedor', 'VEN-950512-MX5', 'vendedor5@zapateria3h.mx', 1, 'Disponible', NULL, 5);

INSERT IGNORE INTO productos (sku, modelo, categoria, segmento, precio, ubicacion, activo) VALUES
('001234', 'Tenis casual azul', 'Casual', 'Hombre', 649.00, 'Pasillo B · Estante 03 · Sección 2', 1),
('001235', 'Tenis deportivo blanco', 'Deportivo', 'Hombre', 899.00, 'Pasillo B · Estante 03 · Sección 2', 1),
('001236', 'Tenis negro running', 'Deportivo', 'Hombre', 799.00, 'Pasillo A · Estante 01 · Sección 1', 1),
('001237', 'Zapatilla rosa', 'Casual', 'Mujer', 579.00, 'Pasillo C · Estante 02 · Sección 4', 1),
('001240', 'Zapato formal café', 'Formal', 'Hombre', 1099.00, 'Pasillo D · Estante 05 · Sección 3', 1),
('001241', 'Bota vaquera', 'Bota', 'Hombre', 1399.00, 'Pasillo E · Estante 01 · Sección 2', 1),
('001242', 'Sandalia de piel', 'Sandalia', 'Mujer', 499.00, 'Pasillo C · Estante 04 · Sección 1', 1),
('001243', 'Tenis infantil luces', 'Deportivo', 'Niño', 489.00, 'Pasillo A · Estante 02 · Sección 3', 1),
('001244', 'Zapato escolar negro', 'Formal', 'Niño', 549.00, 'Pasillo A · Estante 03 · Sección 1', 1),
('001245', 'Tenis urbano blanco dama', 'Casual', 'Mujer', 699.00, 'Pasillo C · Estante 01 · Sección 2', 1);

INSERT IGNORE INTO inventario_tallas (sku, talla, existencia) VALUES
('001234','24',0),('001234','24.5',2),('001234','25',4),('001234','25.5',1),('001234','26',3),('001234','26.5',0),('001234','27',2),('001234','27.5',4),('001234','28',1),
('001235','24',3),('001235','24.5',0),('001235','25',2),('001235','25.5',4),('001235','26',1),('001235','26.5',3),('001235','27',0),('001235','27.5',2),('001235','28',4),
('001236','24',1),('001236','24.5',3),('001236','25',0),('001236','25.5',2),('001236','26',4),('001236','26.5',1),('001236','27',3),('001236','27.5',0),('001236','28',2),
('001237','24',4),('001237','24.5',1),('001237','25',3),('001237','25.5',0),('001237','26',2),('001237','26.5',4),('001237','27',1),('001237','27.5',3),('001237','28',0),
('001240','24',2),('001240','24.5',4),('001240','25',1),('001240','25.5',3),('001240','26',0),('001240','26.5',2),('001240','27',4),('001240','27.5',1),('001240','28',3),
('001241','24',0),('001241','24.5',2),('001241','25',4),('001241','25.5',1),('001241','26',3),('001241','26.5',0),('001241','27',2),('001241','27.5',4),('001241','28',1),
('001242','24',3),('001242','24.5',0),('001242','25',2),('001242','25.5',4),('001242','26',1),('001242','26.5',3),('001242','27',0),('001242','27.5',2),('001242','28',4),
('001243','24',1),('001243','24.5',3),('001243','25',0),('001243','25.5',2),('001243','26',4),('001243','26.5',1),('001243','27',3),('001243','27.5',0),('001243','28',2),
('001244','24',4),('001244','24.5',1),('001244','25',3),('001244','25.5',0),('001244','26',2),('001244','26.5',4),('001244','27',1),('001244','27.5',3),('001244','28',0),
('001245','24',2),('001245','24.5',4),('001245','25',1),('001245','25.5',3),('001245','26',0),('001245','26.5',2),('001245','27',4),('001245','27.5',1),('001245','28',3);

INSERT IGNORE INTO proveedores (id_proveedor, nombre, contacto, telefono, correo, categoria_calzado, activo) VALUES
(1, 'Calzados del Sureste S.A. de C.V.', 'Lic. Roberto Morales', '993 123 4567', 'ventas@calzadosureste.mx', 'Casual y Escolar', 1),
(2, 'Distribuidora de Calzado Deportivo León', 'Ing. Sofía Carrillo', '477 987 6543', 'contacto@deportivoleon.com', 'Deportivo y Running', 1);
