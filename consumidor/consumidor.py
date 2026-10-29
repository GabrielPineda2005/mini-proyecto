"""
Consumidor de transacciones bancarias.

Toma la transaccion PENDIENTE mas antigua, simula su procesamiento y la marca
como PROCESADO. Para que dos consumidores no procesen la misma transaccion,
la seccion critica se protege con un semaforo (mutex) en Redis:

    SET semaforo:transacciones <token_unico> NX EX 10

  - NX: solo se crea la llave si no existe (solo un consumidor la obtiene).
  - EX 10: expira sola a los 10 s, por si el consumidor muere sin liberarla.

La liberacion es segura: solo se borra la llave si el valor sigue siendo
nuestro token (script Lua atomico), para no liberar el semaforo de otro.

Con USAR_SEMAFORO=false se salta el semaforo (Escenario A, condicion de carrera).
"""

import os
import random
import time
import uuid
from datetime import datetime

import mysql.connector
import redis

# Configuracion tomada del archivo .env (via docker-compose)
ID_CONSUMIDOR = os.getenv("ID_CONSUMIDOR", "consumidor")
USAR_SEMAFORO = os.getenv("USAR_SEMAFORO", "true").lower() == "true"

DB_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "mysql"),
    "user": os.getenv("MYSQL_USER", "app"),
    "password": os.getenv("MYSQL_PASSWORD", "app123"),
    "database": os.getenv("MYSQL_DATABASE", "banco"),
    "autocommit": True,  # cada sentencia se confirma de inmediato
}

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

LLAVE_SEMAFORO = "semaforo:transacciones"
TTL_SEMAFORO = 10  # segundos

# Borra la llave solo si su valor es nuestro token (get + del en un solo paso)
LUA_LIBERAR = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def log(mensaje):
    """Imprime una traza con la hora actual (incluye milisegundos)."""
    hora = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{hora}] {ID_CONSUMIDOR.upper():<12} | {mensaje}", flush=True)


def conectar_mysql():
    """Conecta a MySQL, reintentando mientras la base no este lista."""
    while True:
        try:
            conexion = mysql.connector.connect(**DB_CONFIG)
            log("conectado a MySQL")
            return conexion
        except mysql.connector.Error as error:
            log(f"MySQL no disponible ({error.errno}), reintentando en 3 s...")
            time.sleep(3)


def conectar_redis():
    """Conecta a Redis, reintentando mientras no responda."""
    while True:
        try:
            cliente = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
            cliente.ping()
            log("conectado a Redis")
            return cliente
        except redis.exceptions.ConnectionError:
            log("Redis no disponible, reintentando en 3 s...")
            time.sleep(3)


def adquirir_semaforo(r, token):
    """Intenta tomar el semaforo. Devuelve True si lo obtuvo."""
    return r.set(LLAVE_SEMAFORO, token, nx=True, ex=TTL_SEMAFORO) is True


def liberar_semaforo(r, liberar, token):
    """Libera el semaforo solo si seguimos siendo los duenos."""
    if liberar(keys=[LLAVE_SEMAFORO], args=[token]) == 1:
        log("LIBERAR   | semaforo liberado")
    else:
        log("LIBERAR   | el semaforo ya no era mio (expiro), no se toca")


def procesar_una(conexion):
    """
    Procesa la transaccion PENDIENTE mas antigua.
    Devuelve el id procesado, o None si no habia pendientes.
    """
    cursor = conexion.cursor()

    cursor.execute(
        "SELECT id, cuenta, tipo, monto FROM transacciones "
        "WHERE estado = 'PENDIENTE' ORDER BY id LIMIT 1"
    )
    fila = cursor.fetchone()
    if fila is None:
        cursor.close()
        return None

    id_tx, cuenta, tipo, monto = fila
    log(f"PROCESAR  | id={id_tx:<4} {cuenta} {tipo} Q{monto} ...")

    # Simula el trabajo de procesar la transaccion
    time.sleep(random.uniform(1, 3))

    cursor.execute(
        "UPDATE transacciones SET estado = 'PROCESADO', procesado_en = NOW(), "
        "procesado_por = %s WHERE id = %s",
        (ID_CONSUMIDOR, id_tx),
    )
    # Bitacora para detectar procesamientos duplicados
    cursor.execute(
        "INSERT INTO procesamientos (transaccion_id, consumidor, con_semaforo) "
        "VALUES (%s, %s, %s)",
        (id_tx, ID_CONSUMIDOR, USAR_SEMAFORO),
    )
    cursor.close()
    log(f"PROCESADO | id={id_tx:<4} -> PROCESADO")
    return id_tx


def ciclo_con_semaforo(conexion, r, liberar):
    """Una vuelta del ciclo protegida por el semaforo de Redis."""
    token = f"{ID_CONSUMIDOR}:{uuid.uuid4()}"

    if not adquirir_semaforo(r, token):
        dueno = r.get(LLAVE_SEMAFORO) or "-"
        log(f"ESPERAR   | semaforo ocupado por {dueno.split(':')[0]}")
        time.sleep(random.uniform(0.3, 0.6))
        return

    log("ADQUIRIR  | semaforo obtenido, entro a la seccion critica")
    try:
        id_tx = procesar_una(conexion)
    finally:
        # Se libera siempre, aunque haya fallado el procesamiento
        liberar_semaforo(r, liberar, token)

    if id_tx is None:
        log("ESPERAR   | no hay transacciones pendientes")
        time.sleep(2)
    else:
        # Pausa breve para darle oportunidad al otro consumidor
        time.sleep(random.uniform(0.5, 1))


def ciclo_sin_semaforo(conexion):
    """Una vuelta del ciclo SIN proteccion (Escenario A)."""
    if procesar_una(conexion) is None:
        log("ESPERAR   | no hay transacciones pendientes")
        time.sleep(2)
    else:
        time.sleep(random.uniform(0.5, 1))


def main():
    log(f"iniciando (USAR_SEMAFORO={USAR_SEMAFORO})")
    conexion = conectar_mysql()
    r = conectar_redis()
    liberar = r.register_script(LUA_LIBERAR)

    while True:
        try:
            if USAR_SEMAFORO:
                ciclo_con_semaforo(conexion, r, liberar)
            else:
                ciclo_sin_semaforo(conexion)
        except mysql.connector.Error as error:
            log(f"error de MySQL: {error}, reconectando...")
            conexion = conectar_mysql()
        except redis.exceptions.ConnectionError:
            log("se perdio Redis, reconectando...")
            r = conectar_redis()
            liberar = r.register_script(LUA_LIBERAR)


if __name__ == "__main__":
    main()
