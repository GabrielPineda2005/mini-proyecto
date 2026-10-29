# Informe de justificación

**Proyecto:** Productor y consumidores con semáforo en Redis y MySQL
**Curso:** Sistemas Operativos I
**Autor:** Gabriel Pineda
**Repositorio:** https://github.com/GabrielPineda2005/mini-proyecto

## 1. Resumen

Se construyó un sistema de 5 contenedores con Docker Compose. Un productor inserta transacciones bancarias `PENDIENTE` en MySQL cada 1 a 2 segundos, y dos consumidores las procesan y las marcan como `PROCESADO`. La tabla de MySQL funciona como canal entre productor y consumidores, y el acceso de escritura de los consumidores se controla con un semáforo binario (mutex) en Redis. Se ejecutaron dos escenarios de al menos 20 transacciones: sin semáforo (A) y con semáforo (B).

## 2. ¿Por qué hace falta un semáforo?

La operación del consumidor tiene tres pasos: leer la transacción pendiente más antigua, procesarla y actualizarla. Estos pasos no son atómicos: entre la lectura y la actualización pasan de 1 a 3 segundos. Si los dos consumidores leen en ese intervalo, ambos ven la misma fila como `PENDIENTE` y ambos la procesan. Es una **condición de carrera** sobre un recurso compartido. En un banco, eso equivale a aplicar dos veces el mismo depósito o retiro.

El conjunto "leer, procesar y actualizar" es la **sección crítica**. El semáforo garantiza **exclusión mutua**: solo un consumidor a la vez puede estar dentro de ella.

## 3. ¿Por qué Redis y cómo se implementa el semáforo?

Los consumidores son procesos en contenedores distintos, sin memoria compartida, así que un semáforo del sistema operativo o de Python no sirve entre ellos. Se necesita un **semáforo distribuido** en un servicio que todos puedan consultar. Redis es adecuado porque ejecuta cada comando de forma atómica en un solo hilo, trabaja en memoria (es muy rápido) y permite que las llaves expiren.

**Adquisición:** `SET semaforo:transacciones <token> NX EX 10`

- **`NX`** (*only if Not eXists*): la llave solo se crea si no existe. Si dos consumidores lo intentan al mismo tiempo, Redis atiende los comandos uno tras otro, de modo que solo uno obtiene `OK` y el otro recibe `nil`. Así, la comprobación y la asignación ocurren en una sola operación atómica, equivalente al `wait()` (operación P) de un semáforo binario.
- **`EX 10`**: la llave expira sola a los 10 segundos. Si un consumidor falla dentro de la sección crítica, el semáforo no queda tomado para siempre y se evita un **bloqueo permanente** (*deadlock*). Se eligieron 10 s porque el procesamiento más largo dura 3 s, lo que deja un margen amplio.
- **`<token>`**: valor único por cada adquisición (`consumidor1:<uuid>`), que identifica al dueño del semáforo.

**Liberación segura**, equivalente al `signal()` (operación V):

```lua
if redis.call("get", KEYS[1]) == ARGV[1] then return redis.call("del", KEYS[1]) else return 0 end
```

Un `DEL` directo sería peligroso. Si el semáforo de un consumidor expiró (por ejemplo, porque su procesamiento se retrasó más de 10 s) y otro consumidor ya lo adquirió, el `DEL` del primero borraría el semáforo del segundo y dejaría entrar a un tercero a la sección crítica. Por eso se compara el token antes de borrar. Además, la comparación y el borrado se hacen en un **script Lua**, que Redis ejecuta de forma atómica: entre el `GET` y el `DEL` no puede intervenir otro cliente.

**Espera:** si el consumidor no obtiene el semáforo, imprime `ESPERAR` y reintenta tras 0.3 a 0.6 s (espera activa con pausas). Después de procesar, cada consumidor hace una pausa de 0.5 a 1 s antes de volver a intentarlo, para darle oportunidad al otro y evitar la **inanición** (*starvation*).

## 4. ¿Por qué el productor no usa el semáforo?

El productor solo ejecuta `INSERT` de filas nuevas y nunca modifica filas existentes, así que no compite con los consumidores por los mismos datos. MySQL asigna los `id` con `AUTO_INCREMENT` de forma segura ante inserciones concurrentes. Hacer que el productor tomara el semáforo solo reduciría el rendimiento, sin ningún beneficio.

## 5. Resultados

| | Escenario A (sin semáforo) | Escenario B (con semáforo) |
|---|---|---|
| Duración | 57 s | 57 s |
| Transacciones distintas procesadas | 22 | 24 |
| Procesamientos registrados | 42 | 24 |
| **Procesamientos duplicados** | **20** | **0** |
| Reparto entre consumidores | Casi todas procesadas por ambos | 12 / 12 |
| Espera promedio (creado → procesado) | 14.5 s | 11.5 s |
| Semáforo en Redis | Siempre vacío | Alterna entre `consumidor1:…` y `consumidor2:…`, TTL ≤ 10 |

Los duplicados se detectaron con la tabla `procesamientos`, que registra cada procesamiento. En la tabla `transacciones` no se ven, porque el segundo `UPDATE` sobrescribe el `procesado_por` del primero: el error queda oculto en los datos finales.

**Escenario A.** La traza muestra la condición de carrera con claridad:

```
22:12:06.645  consumidor1  PROCESAR  id=1
22:12:06.799  consumidor2  PROCESAR  id=1     (0.15 s después, la misma transacción)
```

Ambos consumidores trabajan en paralelo, pero sobre las mismas filas: de 42 procesamientos, solo 22 fueron trabajo útil. El paralelismo no aporta nada si no hay coordinación.

**Escenario B.** Las trazas siguen siempre el patrón `ADQUIRIR → PROCESAR → PROCESADO → LIBERAR`. Mientras tanto, el otro consumidor imprime `ESPERAR | semaforo ocupado por consumidorX`. Nunca hay dos consumidores dentro de la sección crítica al mismo tiempo, cada transacción se procesa exactamente una vez y la carga se reparte por igual (12/12).

## 6. Análisis y limitaciones

- **Costo de la exclusión mutua.** Con semáforo, el procesamiento queda **serializado**: los dos consumidores juntos procesan una transacción a la vez, al ritmo de un solo consumidor (unos 2 s más una pausa). El productor genera una cada 1.5 s en promedio, así que las pendientes se acumulan: al final del escenario B quedaron 13. Se gana correctitud a cambio de rendimiento.
- **Granularidad del bloqueo.** Un único semáforo global protege toda la tabla. Una mejora sería bloquear por transacción (una llave por `id`) o usar los bloqueos de fila de MySQL (`SELECT ... FOR UPDATE SKIP LOCKED`), para que cada consumidor tome una fila distinta y ambos trabajen en paralelo sin duplicados.
- **Expiración vs. duración del trabajo.** Si un procesamiento superara los 10 s del TTL, el semáforo expiraría con el trabajo aún en curso. La liberación segura evita borrar el semáforo ajeno, pero no evita que otro consumidor entre. En un sistema real habría que renovar el TTL periódicamente o hacer el `UPDATE` condicional (`WHERE estado = 'PENDIENTE'`).
- **Punto único de falla.** Si Redis cae, los consumidores no pueden coordinarse. El código reintenta la conexión, pero no procesa nada mientras tanto.

## 7. Conclusiones

1. Sin sincronización, dos procesos que leen y modifican el mismo recurso producen resultados incorrectos: en el escenario A, el 48 % de los procesamientos (20 de 42) fueron duplicados.
2. Un semáforo binario en Redis, implementado con `SET NX EX` y liberación segura mediante Lua, garantiza la exclusión mutua entre procesos de contenedores distintos: en el escenario B hubo 0 duplicados.
3. Las tres decisiones de diseño son necesarias: `NX` para la atomicidad, `EX` contra los bloqueos permanentes y la liberación por token para no soltar el semáforo de otro.
4. La exclusión mutua tiene un costo de rendimiento, porque serializa la sección crítica. Conviene que esa sección sea lo más pequeña posible y que el bloqueo sea tan fino como el problema lo permita.
5. Docker Compose permite levantar toda la infraestructura con un solo comando (`docker compose up -d --build`), con healthchecks que ordenan el arranque y una tabla que se crea automáticamente.
