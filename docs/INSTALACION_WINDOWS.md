# Instalación y operación en Windows Server

Guía para desplegar PTNT-BAL en un servidor Windows (on-premise, single-host),
que es el entorno objetivo de la especificación.

## 1. Requisitos

| Componente | Versión mínima | Notas |
|---|---|---|
| Windows Server | 2019 / 2022 | También Windows 10/11 para pruebas |
| Python | 3.11 x64 | Marcar "Add python.exe to PATH" al instalar |
| RAM | 16 GB (48 GB recomendado para volumetría real) | DuckDB usa `memory_limit` configurable |
| Disco | SSD, ≥ 100 GB libres | Parquet + DuckDB + salidas |
| ODBC (si se lee de SQL Server) | *ODBC Driver 18 for SQL Server* | [Descarga de Microsoft](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server) |
| Cliente Oracle (si aplica) | Instant Client 19+ | Solo si `oracledb` opera en modo "thick" |

## 2. Instalación paso a paso (PowerShell)

Abrir **PowerShell como Administrador** en la carpeta del proyecto:

```powershell
# 1. Crear entorno virtual aislado
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Actualizar pip e instalar el paquete con todos los extras
python -m pip install --upgrade pip
pip install -e ".[all]"

# 3. Verificar la instalación
ptnt --help
ptnt verificar-config -c config\base.yaml
```

Existe un script que automatiza estos pasos:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
```

## 3. Configurar credenciales de las bases de origen (sin escribirlas en disco)

Las credenciales **no van en el YAML**. Se definen como variables de entorno de la
máquina/servicio. En PowerShell, para persistirlas a nivel de máquina:

```powershell
# Ejecutar como Administrador; ámbito Machine para que el servicio las vea
[Environment]::SetEnvironmentVariable("PTNT_SRC_COMERCIAL_USER","svc_ptnt","Machine")
[Environment]::SetEnvironmentVariable("PTNT_SRC_COMERCIAL_PASS","<secreto>","Machine")
[Environment]::SetEnvironmentVariable("PTNT_SRC_SIG_USER","svc_sig","Machine")
[Environment]::SetEnvironmentVariable("PTNT_SRC_SIG_PASS","<secreto>","Machine")
[Environment]::SetEnvironmentVariable("PTNT_JWT_SECRET","<clave-larga-aleatoria>","Machine")
```

Probar la conectividad:

```powershell
ptnt probar-fuentes -c config\base.yaml
```

> Para entornos con mayor exigencia, ver [SEGURIDAD.md](SEGURIDAD.md): uso de
> Windows Credential Manager / DPAPI en lugar de variables de entorno.

## 4. Ejecutar el análisis

```powershell
# Con datos reales (fuente 'comercial_csv' del YAML o --csv explícito)
ptnt analizar --csv D:\datos\comercial\consumos_36m.csv

# Sin datos reales todavía: generar sintéticos con hurtos inyectados
ptnt generar-sinteticos -o data\entrada\consumos_36m.csv --clientes 5000
ptnt analizar --csv data\entrada\consumos_36m.csv
```

## 5. Interfaces web

```powershell
# Crear usuarios (contraseña por prompt, se guarda solo el hash)
ptnt crear-usuario analista --rol analyst
ptnt crear-usuario jefatura --rol viewer

# Tablero de escritorio (analista)
scripts\run_dashboard.bat        # http://127.0.0.1:8501

# Visor de solo lectura (terceros)
scripts\run_visor.bat            # http://127.0.0.1:8080
```

## 6. Ejecución programada (Programador de tareas de Windows)

Para recalcular cada noche:

```powershell
$accion  = New-ScheduledTaskAction -Execute "D:\ptnt\.venv\Scripts\ptnt.exe" `
           -Argument "analizar --csv D:\datos\comercial\consumos_36m.csv" `
           -WorkingDirectory "D:\ptnt"
$disparo = New-ScheduledTaskTrigger -Daily -At 2:00AM
Register-ScheduledTask -TaskName "PTNT-Analisis" -Action $accion -Trigger $disparo `
           -RunLevel Highest -User "SYSTEM"
```

## 7. Ejecutar las interfaces como servicio (NSSM)

Para que el visor esté siempre disponible, usar
[NSSM](https://nssm.cc/) (Non-Sucking Service Manager):

```powershell
nssm install PTNT-Visor "D:\ptnt\.venv\Scripts\ptnt.exe" "servir-visor -c D:\ptnt\config\base.yaml"
nssm set PTNT-Visor AppDirectory "D:\ptnt"
nssm set PTNT-Visor Start SERVICE_AUTO_START
nssm start PTNT-Visor
```

Repetir para el tablero (`PTNT-Dashboard` con el argumento `dashboard`).

## 8. Firewall

El visor y el tablero escuchan por defecto en `127.0.0.1` (solo local). Para
exponerlos a la red interna, cambiar `host` en `config\base.yaml` a `0.0.0.0` y
abrir el puerto **solo hacia la red corporativa**:

```powershell
New-NetFirewallRule -DisplayName "PTNT Visor" -Direction Inbound -LocalPort 8080 `
  -Protocol TCP -Action Allow -RemoteAddress 10.0.0.0/8
```

Complementar con `seguridad.redes_permitidas` en el YAML (ver SEGURIDAD.md).

## 9. Recuperación ante fallo

- Los resultados se persisten en `data\ptnt.duckdb` y `outputs\`. Respaldar ambos.
- Una corrida interrumpida no corrompe la base: la persistencia usa
  `CREATE OR REPLACE`. Basta volver a ejecutar `ptnt analizar`.
- Si `data\ptnt.duckdb` se daña, eliminarlo y reejecutar: el esquema se recrea.
