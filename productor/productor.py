"""
Productor de transacciones bancarias.

Cada 1 a 2 segundos inserta en MySQL una transaccion con datos aleatorios
y estado PENDIENTE. No usa el semaforo de Redis: solo inserta filas nuevas
y nunca modifica las existentes, asi que no compite con los consumidores.
"""

import os
import random
import time
from datetime import datetime

import mysql.connector

# Configuracion tomada del archivo .env (via docker-compose)
DB_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "mysql"),
    "user": os.getenv("MYSQL_USER", "app"),
    "password": os.getenv("MYSQL_PASSWORD", "app123"),
    "database": os.getenv("MYSQL_DATABASE", "banco"),
    "autocommit": True,  # cada INSERT se confirma de inmediato
}

TIPOS = ["DEPOSITO", "RETIRO", "TRANSFERENCIA"]


def log(mensaje):
    """Imprime una traza con la hora actual (incluye milisegundos)."""
    hora = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{hora}] PRODUCTOR | {mensaje}", flush=True)


def conectar():
    """Conecta a MySQL, reintentando mientras la base no este lista."""
    while True:
        try:
            conexion = mysql.connector.connect(**DB_CONFIG)
            log("conectado a MySQL")
            return conexion
        except mysql.connector.Error as error:
            log(f"MySQL no disponible ({error.errno}), reintentando en 3 s...")
            time.sleep(3)


def generar_transaccion():
    """Genera los datos aleatorios de una transaccion."""
    cuenta = f"{random.randint(1000, 1009)}-{random.randint(1000, 9999)}"
    tipo = random.choice(TIPOS)
    monto = round(random.uniform(10, 5000), 2)
    return cuenta, tipo, monto


def main():
    conexion = conectar()
    cursor = conexion.cursor()

    while True:
        cuenta, tipo, monto = generar_transaccion()

        try:
            # El estado no se envia: la tabla lo pone en PENDIENTE por defecto
            cursor.execute(
                "INSERT INTO transacciones (cuenta, tipo, monto) VALUES (%s, %s, %s)",
                (cuenta, tipo, monto),
            )
            log(f"insertada id={cursor.lastrowid:<4} | {cuenta} {tipo:<13} Q{monto:>9.2f} | PENDIENTE")
        except mysql.connector.Error as error:
            # Si se cae la conexion, se reconecta y se sigue produciendo
            log(f"error al insertar: {error}")
            conexion = conectar()
            cursor = conexion.cursor()

        # Espera aleatoria de 1 a 2 segundos entre transacciones
        time.sleep(random.uniform(1, 2))


if __name__ == "__main__":
    main()
