# Productor y consumidores con semáforo en Redis y MySQL

Proyecto final de **Sistemas Operativos I**.

Se simula un sistema de transacciones bancarias. Un **productor** inserta transacciones con estado `PENDIENTE` en MySQL y dos **consumidores** las procesan y las marcan como `PROCESADO`. Para que dos consumidores no procesen la misma transacción, la sección crítica se protege con un **semáforo (mutex) distribuido en Redis**.

## Arquitectura

| Contenedor | Imagen | Función |
|---|---|---|
| `mysql` | `mysql:8` | Base de datos con la tabla transaccional |
| `redis` | `redis:7` | Almacena el semáforo |
| `productor` | construida con `productor/Dockerfile` | Inserta una transacción cada 1 a 2 s |
| `consumidor1` | construida con `consumidor/Dockerfile` | Procesa transacciones (con semáforo) |
| `consumidor2` | misma imagen que `consumidor1` | Procesa transacciones (con semáforo) |

```
productor ──INSERT PENDIENTE──► MySQL ◄──SELECT / UPDATE PROCESADO── consumidor1, consumidor2
                                                                           │
                                     Redis (semaforo:transacciones) ◄──────┘ SET NX EX / liberación segura
```

## Estructura

```
mini-proyecto/
├── .env                    Variables de entorno
├── docker-compose.yml      Los 5 contenedores
├── escenario.ps1           Ejecuta los escenarios A y B y guarda evidencias
├── mysql/
│   └── init.sql            Crea las tablas automáticamente
├── productor/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── productor.py
├── consumidor/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── consumidor.py
└── evidencias/
    ├── escenario_A/        Sin semáforo
    └── escenario_B/        Con semáforo
```

## Requisitos

- Docker Desktop (con Docker Compose v2) en ejecución.
- Windows PowerShell, solo para el script de escenarios.

## Despliegue

Desde la carpeta del proyecto:

```powershell
docker compose up -d --build
```

No se necesitan pasos manuales: MySQL crea la base, el usuario y las tablas al arrancar, y el productor y los consumidores esperan a que MySQL y Redis estén `healthy`.

Para comprobar el estado de los contenedores:

```powershell
docker compose ps
```

Para ver los logs en vivo (Ctrl + C para salir):

```powershell
docker compose logs -f productor consumidor1 consumidor2
```

Para apagar todo y borrar los datos:

```powershell
docker compose down -v
```

## Variables de entorno (`.env`)

| Variable | Valor | Descripción |
|---|---|---|
| `MYSQL_ROOT_PASSWORD` | `root123` | Contraseña del usuario root de MySQL |
| `MYSQL_DATABASE` | `banco` | Base de datos que se crea al arrancar |
| `MYSQL_USER` | `app` | Usuario de la aplicación |
| `MYSQL_PASSWORD` | `app123` | Contraseña del usuario de la aplicación |
| `MYSQL_HOST` | `mysql` | Host de MySQL (nombre del servicio en la red de Compose) |
| `REDIS_HOST` | `redis` | Host de Redis |
| `REDIS_PORT` | `6379` | Puerto de Redis |
| `USAR_SEMAFORO` | `true` | `true`: consumidores con semáforo. `false`: sin semáforo |
| `TZ` | `America/Guatemala` | Zona horaria de los contenedores |

`ID_CONSUMIDOR` se define por servicio en `docker-compose.yml` (`consumidor1` y `consumidor2`).

> Las contraseñas son de prueba y se incluyen en el repositorio para que el proyecto se despliegue sin pasos adicionales.

## Base de datos

**`transacciones`**: tabla transaccional.

| Columna | Descripción |
|---|---|
| `id` | Identificador autoincremental |
| `cuenta`, `tipo`, `monto` | Datos aleatorios generados por el productor |
| `estado` | `PENDIENTE` al insertar, `PROCESADO` al terminar |
| `creado_en` | Hora de inserción |
| `procesado_en`, `procesado_por` | Hora de procesamiento y consumidor que la procesó |

**`procesamientos`**: bitácora con una fila por cada vez que un consumidor procesa una transacción. Permite detectar procesamientos duplicados, que en `transacciones` quedarían ocultos porque el segundo `UPDATE` sobrescribe al primero.

## Funcionamiento del semáforo

Cada consumidor repite el siguiente ciclo:

1. Intenta adquirir el semáforo: `SET semaforo:transacciones <token_unico> NX EX 10`.
2. Si no lo obtiene, imprime `ESPERAR` y reintenta tras una pausa corta.
3. Si lo obtiene (sección crítica): selecciona la transacción `PENDIENTE` más antigua, simula el procesamiento (1 a 3 s) y la actualiza a `PROCESADO`.
4. Libera el semáforo **solo si sigue siendo el dueño**, con un script Lua que compara el token y borra la llave en un solo paso atómico.
5. Si no hay pendientes, libera el semáforo y espera 2 s.

El productor no usa el semáforo, porque solo inserta filas nuevas y nunca modifica las existentes.

## Escenarios de prueba

| Escenario | `USAR_SEMAFORO` | Qué demuestra |
|---|---|---|
| A | `false` | Condición de carrera: los dos consumidores procesan la misma transacción |
| B | `true` | Exclusión mutua: cada transacción se procesa exactamente una vez |

### Forma automática (recomendada)

El script reinicia los datos, levanta los contenedores, espera a que se procesen al menos 20 transacciones, detiene la prueba y guarda las evidencias en `evidencias/escenario_X/`:

```powershell
powershell -ExecutionPolicy Bypass -File .\escenario.ps1 -Escenario A
```

```powershell
powershell -ExecutionPolicy Bypass -File .\escenario.ps1 -Escenario B
```

Archivos generados por escenario:

| Archivo | Contenido |
|---|---|
| `traza_completa.log` | Logs del productor y los consumidores ordenados por hora |
| `tabla_eventos.csv` | Tabla de eventos: hora, quién, evento e id de transacción |
| `tabla_tiempos.txt` | Por transacción: creación, procesamiento, espera y veces procesada |
| `verificacion_sql.txt` | Consultas SQL de verificación |
| `estado_redis.txt` | Valor y TTL del semáforo muestreados durante la prueba |
| `resumen.txt` | Duración, procesamientos, duplicados y espera promedio |

### Forma manual

Escenario A (sin semáforo):

```powershell
$env:USAR_SEMAFORO="false"; docker compose down -v; docker compose up -d --build
```

Escenario B (con semáforo):

```powershell
$env:USAR_SEMAFORO="true"; docker compose down -v; docker compose up -d --build
```

## Consultas de verificación

Transacciones por estado:

```powershell
docker exec -it mysql mysql -uapp -papp123 banco -e "SELECT estado, COUNT(*) FROM transacciones GROUP BY estado;"
```

Transacciones procesadas más de una vez (vacío en el escenario B):

```powershell
docker exec -it mysql mysql -uapp -papp123 banco -e "SELECT transaccion_id, COUNT(*) veces, GROUP_CONCAT(consumidor) FROM procesamientos GROUP BY transaccion_id HAVING veces > 1;"
```

Dueño actual del semáforo y tiempo restante:

```powershell
docker exec -it redis redis-cli GET semaforo:transacciones
```

```powershell
docker exec -it redis redis-cli TTL semaforo:transacciones
```

## Resultados obtenidos

| | Escenario A | Escenario B |
|---|---|---|
| Transacciones distintas procesadas | 22 | 24 |
| Procesamientos registrados | 42 | 24 |
| Procesamientos duplicados | **20** | **0** |
| Reparto entre consumidores | Casi todas procesadas por ambos | 12 / 12 |
| Espera promedio (creado → procesado) | 14.5 s | 11.5 s |

## Problemas comunes

- **`docker compose ps` aparece vacío**: los contenedores están apagados (por ejemplo, después de reiniciar la PC). Abre Docker Desktop y ejecuta `docker compose up -d`.
- **`no configuration file provided`**: el comando se ejecutó fuera de la carpeta del proyecto.
- **Cambié `init.sql` y no se aplica**: el script solo se ejecuta con la base vacía. Ejecuta `docker compose down -v` y vuelve a levantar.
