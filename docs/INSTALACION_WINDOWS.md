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
| Chromium/Chrome (si se generan PDF) | Cualquier versión reciente | Los informes se imprimen con `--headless`; sin él salen solo en HTML |

**Para compilar la aplicación móvil** (opcional, solo si se va a generar el APK
en este mismo servidor):

| Componente | Versión | Notas |
|---|---|---|
| JDK | 17 | Temurin o el de Android Studio |
| Android SDK | API 35 | `sdkmanager "platforms;android-35" "build-tools;35.0.0"` |

No hace falta ninguna clave de API ni licencia: el mapa es MapLibre y la
cartografía viaja dentro del paquete de trabajo.

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

## 5.bis Servicio de campo y aplicación móvil

El técnico necesita alcanzar el servidor desde la calle, así que este servicio
**sí** se expone —a diferencia del tablero, que se queda en local—. Publíquelo
detrás del proxy corporativo con TLS: el token de dispositivo viaja en cada
petición y en claro sería robable en cualquier red abierta.

```powershell
# 1. Alta de las cuadrillas (contraseña por prompt; se guarda solo el hash)
ptnt campo-usuario jperez --nombre "Juan Pérez" --unidad CNEL-GYE
ptnt campo-usuario mcedeno --nombre "María Cedeño" --unidad CNEL-GYE

# 2. Definir y repartir el trabajo del día
ptnt campo-repartir --usuarios jperez,mcedeno --top 30 --criterio kwh --aplicar

# 3. Armar los paquetes (con cartografía offline si se tiene)
ptnt campo-paquetes --teselas D:\cartografia\gye.mbtiles

# 4. Servicio de sincronización
ptnt campo-servir --host 0.0.0.0 --puerto 8090
```

Como servicio permanente:

```powershell
nssm install PTNT-Campo "D:\ptnt\.venv\Scripts\ptnt.exe" `
     "campo-servir --host 0.0.0.0 --puerto 8090 -c D:\ptnt\config\base.yaml"
nssm set PTNT-Campo AppDirectory "D:\ptnt"
nssm set PTNT-Campo Start SERVICE_AUTO_START
nssm start PTNT-Campo
```

**Qué respaldar de campo**, y por qué importa más que el resto: `outputs\campo\`
contiene el registro de asignaciones (`registro.db`), los paquetes de retorno tal
como llegaron (`entrantes\`) y el histórico de modificaciones. Los cálculos se
rehacen en minutos; **una jornada de trabajo de campo no se rehace**.

### Compilar el APK

```powershell
cd D:\ptnt\mobile
.\gradlew.bat assembleRelease
# Sale en app\build\outputs\apk\release\
```

La distribución interna se hace por MDM o por descarga directa desde la intranet.
Firme el APK con la clave de la distribuidora y **no la versione**: un APK firmado
con esa clave se instala como si fuera oficial.

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

```powershell
# El servicio de campo sí necesita alcance externo: publíquelo tras el proxy
# corporativo con TLS, nunca directo a Internet.
New-NetFirewallRule -DisplayName "PTNT Campo" -Direction Inbound -LocalPort 8090 `
  -Protocol TCP -Action Allow -RemoteAddress 10.0.0.0/8
```

Complementar con `seguridad.redes_permitidas` en el YAML (ver SEGURIDAD.md).

## 9. Recuperación ante fallo

- Los resultados se persisten en `data\ptnt.duckdb` y `outputs\`. Respaldar ambos.
- Una corrida interrumpida no corrompe la base: la persistencia usa
  `CREATE OR REPLACE`. Basta volver a ejecutar `ptnt analizar`.
- Si `data\ptnt.duckdb` se daña, eliminarlo y reejecutar: el esquema se recrea.
- **El trabajo de campo es la excepción.** `outputs\campo\registro.db` no se
  puede regenerar: guarda a quién se le asignó qué y qué llegó de vuelta.
  Respáldelo con la misma frecuencia con que sale una cuadrilla.
- Un paquete de retorno que llegó con hallazgos bloqueantes queda en
  `outputs\campo\entrantes\`. Se puede volver a procesar tal cual una vez
  resuelto el problema: no hay que pedirle al técnico que repita la jornada.
