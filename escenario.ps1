<#
Ejecuta un escenario de prueba desde cero y guarda las evidencias.

  Escenario A: consumidores SIN semaforo (condicion de carrera)
  Escenario B: consumidores CON semaforo en Redis

Uso (desde la carpeta del proyecto):
  powershell -ExecutionPolicy Bypass -File .\escenario.ps1 -Escenario A
  powershell -ExecutionPolicy Bypass -File .\escenario.ps1 -Escenario B

Las evidencias quedan en evidencias\escenario_A\ o evidencias\escenario_B\.
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("A", "B")]
    [string]$Escenario,

    # Minimo de transacciones distintas procesadas antes de detener la prueba
    [int]$Minimo = 20
)

Set-Location $PSScriptRoot

$semaforo = if ($Escenario -eq "A") { "false" } else { "true" }
$dir = "evidencias\escenario_$Escenario"
New-Item -ItemType Directory -Force $dir | Out-Null

# Consulta MySQL sin el aviso de contrasena en linea de comandos
function Sql([string]$consulta, [string[]]$opciones = @("-t")) {
    docker exec -e MYSQL_PWD=app123 mysql mysql -uapp banco @opciones -e $consulta
}

Write-Host "== Escenario $Escenario (USAR_SEMAFORO=$semaforo) =="

# Sin Docker no hay prueba: se aborta antes de sobrescribir evidencias anteriores
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker no esta en ejecucion. Abre Docker Desktop y vuelve a intentarlo."
    exit 1
}

# 1. Arranque limpio: se borran los datos anteriores
$env:USAR_SEMAFORO = $semaforo
docker compose down -v | Out-Null
docker compose up -d --build | Out-Null

# 2. Esperar a que se procesen al menos $Minimo transacciones distintas
$inicio = Get-Date
$redis = @()
do {
    Start-Sleep 3
    $procesadas = [int](Sql "SELECT COUNT(DISTINCT transaccion_id) FROM procesamientos;" @("-N", "-s"))
    Write-Host ("  {0,3} s -> {1} transacciones procesadas" -f [int]((Get-Date) - $inicio).TotalSeconds, $procesadas)

    # Muestras del estado del semaforo en Redis mientras corre la prueba
    $hora = Get-Date -Format "HH:mm:ss"
    $valor = docker exec redis redis-cli GET semaforo:transacciones
    $ttl = docker exec redis redis-cli TTL semaforo:transacciones
    $redis += "[$hora] GET semaforo:transacciones = '$valor' | TTL = $ttl"
} while ($procesadas -lt $Minimo -and ((Get-Date) - $inicio).TotalMinutes -lt 5)

# 3. Detener productor y consumidores para congelar los resultados
docker compose stop productor consumidor1 consumidor2 | Out-Null
$fin = Get-Date

# 4. Traza completa ordenada por hora
$traza = docker compose logs --no-log-prefix productor consumidor1 consumidor2 |
    Where-Object { $_ -match "^\[" } | Sort-Object
$traza | Out-File "$dir\traza_completa.log" -Encoding utf8

# 5. Tabla de eventos (hora, quien, evento, id) a partir de la traza
$eventos = foreach ($linea in $traza) {
    if ($linea -match "^\[(?<hora>[\d:.]+)\]\s+(?<quien>\S+)\s+\|\s+(?<evento>\S+)\s*\|?\s*(?<detalle>.*)$") {
        # Se copian los grupos antes de otro -match, que sobrescribe $Matches
        $hora = $Matches.hora
        $quien = $Matches.quien.ToLower()
        $evento = $Matches.evento
        $detalle = $Matches.detalle.Trim()
        if ($evento -eq "insertada") { $evento = "INSERTAR" }
        if ($evento -cnotmatch "^[A-Z]+$") { continue }   # descarta mensajes de conexion
        $id = if ($detalle -match "id=(\d+)") { $Matches[1] } else { "" }
        [PSCustomObject]@{
            hora    = $hora
            quien   = $quien
            evento  = $evento
            id      = $id
            detalle = $detalle
        }
    }
}
$eventos | Export-Csv "$dir\tabla_eventos.csv" -NoTypeInformation -Encoding utf8

# 6. Tabla de tiempos por transaccion (espera desde que se creo hasta que se proceso)
Sql @"
SELECT t.id, t.tipo, t.monto, t.estado,
       TIME(t.creado_en) AS creado, TIME(t.procesado_en) AS procesado,
       TIMESTAMPDIFF(SECOND, t.creado_en, t.procesado_en) AS espera_s,
       t.procesado_por,
       COUNT(p.id) AS veces_procesada,
       GROUP_CONCAT(p.consumidor ORDER BY p.id) AS consumidores
FROM transacciones t
LEFT JOIN procesamientos p ON p.transaccion_id = t.id
GROUP BY t.id
ORDER BY t.id;
"@ | Out-File "$dir\tabla_tiempos.txt" -Encoding utf8

# 7. Consultas SQL de verificacion
$verificacion = @(
    "-- Transacciones por estado"
    (Sql "SELECT estado, COUNT(*) AS total FROM transacciones GROUP BY estado;")
    ""
    "-- Transacciones procesadas por cada consumidor"
    (Sql "SELECT procesado_por, COUNT(*) AS total FROM transacciones WHERE estado='PROCESADO' GROUP BY procesado_por;")
    ""
    "-- Procesamientos totales vs transacciones distintas (si difieren, hubo duplicados)"
    (Sql "SELECT COUNT(*) AS procesamientos, COUNT(DISTINCT transaccion_id) AS transacciones_distintas, COUNT(*) - COUNT(DISTINCT transaccion_id) AS duplicados FROM procesamientos;")
    ""
    "-- Transacciones procesadas mas de una vez"
    (Sql "SELECT transaccion_id, COUNT(*) AS veces, GROUP_CONCAT(consumidor ORDER BY id) AS consumidores FROM procesamientos GROUP BY transaccion_id HAVING veces > 1;")
)
$verificacion | Out-File "$dir\verificacion_sql.txt" -Encoding utf8

# 8. Estado del semaforo en Redis durante la prueba
$redis | Out-File "$dir\estado_redis.txt" -Encoding utf8

# 9. Resumen
$conteo = (Sql "SELECT COUNT(*), COUNT(DISTINCT transaccion_id) FROM procesamientos;" @("-N", "-s")) -split "\s+"
$espera = Sql "SELECT ROUND(AVG(TIMESTAMPDIFF(SECOND, creado_en, procesado_en)), 1) FROM transacciones WHERE estado='PROCESADO';" @("-N", "-s")
$resumen = @"
Escenario $Escenario  (USAR_SEMAFORO=$semaforo)
Inicio:   $($inicio.ToString("yyyy-MM-dd HH:mm:ss"))
Fin:      $($fin.ToString("yyyy-MM-dd HH:mm:ss"))
Duracion: $([int]($fin - $inicio).TotalSeconds) s

Procesamientos registrados:   $($conteo[0])
Transacciones distintas:      $($conteo[1])
Procesamientos duplicados:    $([int]$conteo[0] - [int]$conteo[1])
Espera promedio (creado -> procesado): $espera s
"@
$resumen | Out-File "$dir\resumen.txt" -Encoding utf8

Write-Host ""
Write-Host $resumen
Write-Host "Evidencias guardadas en $dir"
