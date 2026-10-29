-- Tabla transaccional: el productor inserta PENDIENTE, los consumidores marcan PROCESADO
CREATE TABLE IF NOT EXISTS transacciones (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    cuenta        VARCHAR(20)    NOT NULL,
    tipo          ENUM('DEPOSITO', 'RETIRO', 'TRANSFERENCIA') NOT NULL,
    monto         DECIMAL(12, 2) NOT NULL,
    estado        ENUM('PENDIENTE', 'PROCESADO') NOT NULL DEFAULT 'PENDIENTE',
    creado_en     DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    procesado_en  DATETIME       NULL,
    procesado_por VARCHAR(50)    NULL
);

-- Bitacora: una fila cada vez que un consumidor procesa una transaccion.
-- Sirve como evidencia: si un mismo transaccion_id aparece mas de una vez,
-- hubo procesamiento duplicado (condicion de carrera).
CREATE TABLE IF NOT EXISTS procesamientos (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    transaccion_id INT          NOT NULL,
    consumidor     VARCHAR(50)  NOT NULL,
    con_semaforo   BOOLEAN      NOT NULL,
    procesado_en   DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
);
