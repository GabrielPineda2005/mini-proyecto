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
