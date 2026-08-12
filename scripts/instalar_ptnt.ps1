<#
.SYNOPSIS
    Deja PTNT-BAL operativo en Windows a partir de un Python que usted indique.

.DESCRIPTION
    Pensado para un servidor con varios Python instalados —típicamente el de
    ArcGIS Pro más algún otro— donde hay que instalar en uno concreto y **no
    romper los demás**.

    Por defecto crea un entorno virtual APARTE que se apoya en el Python que
    usted indique. Es la diferencia entre una instalación reversible y una
    tarde perdida:

      · El entorno de conda de ArcGIS **no se modifica**. Si algo sale mal, se
        borra una carpeta y no queda rastro.
      · `arcpy`, `numpy` y `pandas` se siguen viendo desde el entorno de ArcGIS,
        así que no se reinstalan ni se pisan sus versiones.
      · ArcGIS Pro sigue abriendo igual, pase lo que pase aquí.

    Con -Modo Directo instala dentro del propio entorno de conda. Solo tiene
    sentido si necesita que `ptnt` viva dentro del entorno de ArcGIS por alguna
    razón concreta, y aun así el script protege las versiones que ArcGIS fijó.

.PARAMETER Python
    Ruta al python.exe donde instalar. Para un clon de ArcGIS Pro suele ser:
      C:\Users\<usuario>\AppData\Local\ESRI\conda\envs\<clon>\python.exe

.PARAMETER Destino
    Carpeta del entorno virtual a crear. Por defecto: C:\ptnt\env

.PARAMETER Repo
    Carpeta con el código (donde está pyproject.toml). Por defecto: la carpeta
    padre de este script.

.PARAMETER Modo
    Venv (por defecto, recomendado) o Directo.

.PARAMETER Extras
    Qué instalar. Por defecto "all". Para un servidor mínimo:
    "store,sources,security,test".

.PARAMETER Proxy
    Proxy corporativo, p. ej. http://proxy.empresa.ec:8080

.PARAMETER Ruedas
    Carpeta con archivos .whl para instalación SIN INTERNET.

.PARAMETER SinPruebas
    Omite la ejecución de las pruebas al final. No se recomienda: son 30
    segundos y son la única forma de saber que quedó bien.

.EXAMPLE
    .\instalar_ptnt.ps1 -Python "C:\Users\wbastidas\AppData\Local\ESRI\conda\envs\ptnt\python.exe"

.EXAMPLE
    .\instalar_ptnt.ps1 -Python "C:\...\python.exe" -Proxy "http://proxy:8080" -Extras "store,sources,security,test"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Python,

    [string]$Destino = "C:\ptnt\env",
    [string]$Repo = "",
    [ValidateSet("Venv", "Directo")]
    [string]$Modo = "Venv",
    [string]$Extras = "all",
    [string]$Proxy = "",
    [string]$Ruedas = "",
    [switch]$SinPruebas,
    [switch]$Forzar
)

$ErrorActionPreference = "Stop"
$script:Avisos = @()

# --------------------------------------------------------------------------- #
# Salida legible
# --------------------------------------------------------------------------- #
function Paso  ($n, $t) { Write-Host "`n$('=' * 72)`n  PASO $n. $t`n$('=' * 72)" -ForegroundColor Cyan }
function Ok    ($t) { Write-Host "  [OK]  $t" -ForegroundColor Green }
function Info  ($t) { Write-Host "        $t" -ForegroundColor Gray }
function Aviso ($t) { Write-Host "  [!]   $t" -ForegroundColor Yellow; $script:Avisos += $t }
function Fatal ($t) {
    Write-Host "`n  [ERROR] $t`n" -ForegroundColor Red
    exit 1
}

Write-Host @"

  PTNT-BAL — Instalación en Windows
  ---------------------------------
  Modo: $Modo
"@ -ForegroundColor White

# --------------------------------------------------------------------------- #
Paso 1 "COMPROBAR EL PYTHON QUE USTED INDICÓ"
# --------------------------------------------------------------------------- #
if (-not (Test-Path $Python)) {
    Fatal @"
No existe: $Python

Para encontrar el Python correcto:
  · ArcGIS Pro (base, NO usar para instalar):
      C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe
  · Clones de ArcGIS (aquí es donde debe apuntar):
      C:\Users\<usuario>\AppData\Local\ESRI\conda\envs\<nombre>\python.exe
  · Listar los clones que existen:
      dir "`$env:LOCALAPPDATA\ESRI\conda\envs"
"@
}

$Python = (Resolve-Path $Python).Path
$verTexto = & $Python -c "import sys; print('%d.%d.%d' % sys.version_info[:3])" 2>&1
if ($LASTEXITCODE -ne 0) { Fatal "No se pudo ejecutar $Python`n$verTexto" }

$ver = [version]$verTexto
Info "Python      : $verTexto"
Info "Ubicación   : $Python"

if ($ver -lt [version]"3.11.0") {
    Fatal @"
PTNT-BAL necesita Python 3.11 o superior, y ese es $verTexto.

ArcGIS Pro 3.3 y posteriores traen Python 3.11. Si su clon tiene una versión
anterior, viene de una instalación de ArcGIS Pro más vieja: clone desde el
entorno de la versión actual, o use un Python 3.11 independiente.
"@
}
Ok "Versión compatible"

# ¿Es el entorno base de ArcGIS? Instalar ahí es la forma más rápida de dejar
# ArcGIS Pro sin abrir, y además suele ser de solo lectura.
$esBase = $Python -like "*Program Files\ArcGIS*"
if ($esBase -and $Modo -eq "Directo" -and -not $Forzar) {
    Fatal @"
Está apuntando al entorno BASE de ArcGIS Pro, en modo Directo.

Ese entorno es de solo lectura y es el que usa ArcGIS Pro para arrancar.
Instalar ahí puede dejar Pro sin abrir, y no hay forma cómoda de revertirlo.

Haga un clon primero (ArcGIS Pro → Configuración → Administrador de paquetes →
Clonar entorno), o ejecute este script en modo Venv, que no lo toca.
"@
}
if ($esBase) {
    Aviso "El Python indicado es el base de ArcGIS Pro. En modo Venv no se modifica, pero conviene apuntar a un clon."
}

# Qué trae ya el entorno. Importa porque lo que ya está no se reinstala.
$yaInstalado = @{}
foreach ($p in @("numpy", "pandas", "scipy", "arcpy", "gdal", "pyogrio", "fiona")) {
    $v = & $Python -c "
try:
    import importlib.metadata as m; print(m.version('$p'))
except Exception:
    try:
        import $p; print(getattr($p, '__version__', 'presente'))
    except Exception: print('')
" 2>$null
    if ($v) { $yaInstalado[$p] = $v.Trim() }
}
if ($yaInstalado.Count) {
    Info ""
    Info "Ya presente en ese entorno:"
    foreach ($k in $yaInstalado.Keys | Sort-Object) { Info ("  {0,-10} {1}" -f $k, $yaInstalado[$k]) }
}
if ($yaInstalado.ContainsKey("arcpy")) {
    Ok "arcpy disponible: podrá leer geodatabases también por esa vía"
}

# --------------------------------------------------------------------------- #
Paso 2 "LOCALIZAR EL CÓDIGO"
# --------------------------------------------------------------------------- #
if (-not $Repo) { $Repo = Split-Path -Parent $PSScriptRoot }
if (-not (Test-Path (Join-Path $Repo "pyproject.toml"))) {
    Fatal @"
No encuentro pyproject.toml en: $Repo

Indique la carpeta del código con -Repo, por ejemplo:
  -Repo C:\ptnt\Perdidasfinal
"@
}
$Repo = (Resolve-Path $Repo).Path
Ok "Código en $Repo"

# --------------------------------------------------------------------------- #
Paso 3 "PREPARAR EL ENTORNO DE INSTALACIÓN"
# --------------------------------------------------------------------------- #
if ($Modo -eq "Venv") {
    if (Test-Path $Destino) {
        if (-not $Forzar) {
            Aviso "Ya existe $Destino — se reutiliza. Use -Forzar para rehacerlo desde cero."
        } else {
            Info "Borrando el entorno anterior…"
            Remove-Item -Recurse -Force $Destino
        }
    }
    if (-not (Test-Path $Destino)) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destino) | Out-Null
        Info "Creando entorno virtual (se apoya en el Python indicado, sin modificarlo)…"
        # --system-site-packages es lo que permite seguir viendo arcpy, numpy y
        # pandas del entorno de ArcGIS sin reinstalarlos ni pisar sus versiones.
        & $Python -m venv --system-site-packages $Destino
        if ($LASTEXITCODE -ne 0) { Fatal "No se pudo crear el entorno virtual en $Destino" }
    }
    $Py = Join-Path $Destino "Scripts\python.exe"
    $Scripts = Join-Path $Destino "Scripts"
    Ok "Entorno virtual en $Destino"
    Info "El entorno de conda queda intacto: si algo falla, borre esa carpeta y listo."

    # Comprobación que ahorra una tarde. Un venv creado sobre un Python de conda
    # a veces no encuentra las DLL que numpy trae en Library\bin, porque esas
    # rutas las añade la activación de conda y el venv no la hereda. El síntoma
    # es un "DLL load failed" al importar numpy, que sin este aviso aparece
    # veinte minutos después, a mitad de la instalación y sin pista de la causa.
    if ($yaInstalado.ContainsKey("numpy")) {
        Info "Comprobando que numpy se importa desde el entorno virtual…"
        $errNumpy = & $Py -c "import numpy" 2>&1
        if ($LASTEXITCODE -ne 0) {
            Fatal @"
El entorno virtual no puede cargar numpy del entorno de conda.

Es un problema conocido en Windows: numpy trae sus DLL en Library\bin, y esas
rutas las añade la activación de conda, que un entorno virtual no hereda.

La salida buena es instalar directamente en su CLON de conda. Es seguro
—un clon es desechable, y si se estropea lo vuelve a clonar desde ArcGIS Pro—
y este script protege igualmente las versiones que ArcGIS fijó:

    .\instalar_ptnt.ps1 -Python "$Python" -Modo Directo

Detalle: $($errNumpy | Select-Object -Last 2)
"@
        }
        Ok "numpy se carga correctamente desde el entorno virtual"
    }
} else {
    $Py = $Python
    $Scripts = Join-Path (Split-Path -Parent $Python) "Scripts"
    Aviso "Modo Directo: se instalará DENTRO del entorno de conda. Verifique que sea un clon y no el original."
}

if (-not (Test-Path $Py)) { Fatal "No se generó el intérprete esperado: $Py" }

# --------------------------------------------------------------------------- #
Paso 4 "RED: PROXY O INSTALACIÓN SIN INTERNET"
# --------------------------------------------------------------------------- #
$pipArgs = @()
if ($Proxy) {
    $pipArgs += @("--proxy", $Proxy)
    Ok "Usando proxy: $Proxy"
} elseif ($env:HTTPS_PROXY) {
    Info "Se usará el proxy del sistema: $env:HTTPS_PROXY"
}

if ($Ruedas) {
    if (-not (Test-Path $Ruedas)) { Fatal "No existe la carpeta de ruedas: $Ruedas" }
    $pipArgs += @("--no-index", "--find-links", (Resolve-Path $Ruedas).Path)
    Ok "Instalación sin internet desde $Ruedas"
} else {
    # Sondeo real: fallar aquí con un mensaje claro ahorra diez minutos de
    # timeouts crípticos de pip en un servidor sin salida.
    Info "Comprobando el acceso a PyPI…"
    $prueba = & $Py -m pip download --no-deps --dest $env:TEMP @pipArgs "wheel" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Fatal @"
No hay acceso a PyPI desde este servidor.

Tiene dos salidas:

 1. Proxy corporativo — vuelva a ejecutar con:
      -Proxy "http://proxy.empresa.ec:8080"

 2. Sin internet — descargue las dependencias en un equipo CON internet:
      pip download "ptnt-bal[$Extras] @ file:///$($Repo -replace '\\','/')" -d C:\ruedas
    copie la carpeta C:\ruedas al servidor y ejecute:
      -Ruedas C:\ruedas

Detalle: $($prueba | Select-Object -Last 3)
"@
    }
    Ok "PyPI accesible"
}

# --------------------------------------------------------------------------- #
Paso 5 "PROTEGER LAS VERSIONES QUE ARCGIS FIJÓ"
# --------------------------------------------------------------------------- #
# El riesgo real de instalar sobre un entorno de ArcGIS: pip actualiza numpy o
# pandas para satisfacer una dependencia, y arcpy —compilado contra la versión
# anterior— deja de importar. Con un archivo de restricciones, pip usa lo que ya
# está en vez de actualizarlo, y si de verdad no puede, lo dice en vez de
# romperlo.
$restricciones = Join-Path $env:TEMP "ptnt_restricciones.txt"
$lineas = @()
foreach ($p in @("numpy", "pandas", "scipy")) {
    if ($yaInstalado.ContainsKey($p) -and $yaInstalado[$p] -match '^\d') {
        $lineas += "$p==$($yaInstalado[$p])"
    }
}
if ($lineas.Count) {
    $lineas | Set-Content -Path $restricciones -Encoding ascii
    $pipArgs += @("--constraint", $restricciones)
    Ok "Se conservarán las versiones ya instaladas de: $($lineas -join ', ')"
    Info "Así arcpy sigue funcionando: pip no las va a actualizar por debajo."
} else {
    Info "El entorno no traía numpy/pandas propios: se instalarán limpios."
}

# --------------------------------------------------------------------------- #
Paso 6 "INSTALAR"
# --------------------------------------------------------------------------- #
Info "Actualizando pip…"
& $Py -m pip install --upgrade pip setuptools wheel @pipArgs 2>&1 | Out-Null

Info "Instalando PTNT-BAL con los extras: $Extras"
Info "(esto tarda unos minutos la primera vez)"
$salida = & $Py -m pip install -e "$Repo[$Extras]" @pipArgs 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host ($salida | Select-Object -Last 25) -ForegroundColor DarkGray
    if ($salida -match "Cannot install|ResolutionImpossible|conflict") {
        Fatal @"
Conflicto de versiones con lo que ArcGIS tiene fijado.

Suele significar que el numpy o el pandas del entorno de ArcGIS son más
antiguos de lo que necesita alguna librería. Dos salidas, en este orden:

 1. Modo Venv SIN heredar los paquetes de conda — instala versiones propias y
    deja el entorno de ArcGIS completamente aparte. Pierde el acceso a arcpy
    desde este entorno, que este proyecto no necesita:
      (borre $Destino y vuelva a ejecutar con -Modo Venv y -Forzar,
       tras quitar --system-site-packages del script si desea aislamiento total)

 2. Instale menos extras. El núcleo no necesita casi nada:
      -Extras "store,security,test"
"@
    }
    Fatal "Falló la instalación. Últimas líneas arriba."
}
Ok "PTNT-BAL instalado"

# La lectura de File Geodatabase es opcional y puede fallar por GDAL. Que falle
# no debe tumbar toda la instalación: el resto del sistema funciona sin ella.
if ($Extras -eq "all" -or $Extras -like "*fgdb*") {
    Info "Instalando el lector de File Geodatabase (pyogrio)…"
    & $Py -m pip install "pyogrio>=0.8" @pipArgs 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Aviso "No se pudo instalar pyogrio: la lectura directa de .gdb no estará disponible. El resto funciona. Alternativa: exportar la geodatabase a CSV/GPKG desde ArcGIS Pro."
    } else {
        Ok "Lector de File Geodatabase listo"
    }
}

# --------------------------------------------------------------------------- #
Paso 7 "SECRETOS Y CARPETAS DE TRABAJO"
# --------------------------------------------------------------------------- #
# Las credenciales nunca van al YAML ni al código: el sistema lee nombres de
# variables de entorno. Aquí solo se crea la clave de firma de sesiones.
if (-not [Environment]::GetEnvironmentVariable("PTNT_JWT_SECRET", "Machine")) {
    $bytes = New-Object byte[] 48
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $secreto = [Convert]::ToBase64String($bytes)
    try {
        [Environment]::SetEnvironmentVariable("PTNT_JWT_SECRET", $secreto, "Machine")
        Ok "Clave de firma de sesiones generada (ámbito Máquina)"
        Info "Se guardó en la variable PTNT_JWT_SECRET. No hace falta que la copie."
    } catch {
        Aviso "No se pudo escribir la variable de máquina (¿ejecutó como Administrador?). Créela a mano: setx /M PTNT_JWT_SECRET `"<valor>`""
    }
} else {
    Ok "PTNT_JWT_SECRET ya estaba configurada"
}

foreach ($c in @("data", "outputs", "logs")) {
    $ruta = Join-Path $Repo $c
    if (-not (Test-Path $ruta)) { New-Item -ItemType Directory -Force -Path $ruta | Out-Null }
}
Ok "Carpetas de trabajo listas"

# --------------------------------------------------------------------------- #
Paso 8 "COMPROBAR QUE QUEDÓ OPERATIVO"
# --------------------------------------------------------------------------- #
Push-Location $Repo
try {
    $fallos = @()

    Info "Importando la plataforma…"
    & $Py -c "import ptnt; print(ptnt.__name__)" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { $fallos += "no se puede importar el paquete" } else { Ok "El paquete importa" }

    $ptntExe = Join-Path $Scripts "ptnt.exe"
    if (Test-Path $ptntExe) { Ok "Comando ptnt disponible" }
    else { Aviso "No apareció ptnt.exe en $Scripts; use '$Py -m ptnt.cli' en su lugar" }

    Info "Validando la configuración…"
    & $Py -m ptnt.cli verificar-config 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { $fallos += "la configuración no valida" } else { Ok "Configuración válida" }

    Info "Midiendo los recursos del servidor…"
    & $Py -m ptnt.cli recursos 2>&1 | Select-Object -Last 12 | ForEach-Object { Info $_ }

    if (-not $SinPruebas) {
        Info ""
        Info "Ejecutando las pruebas (unos 40 segundos)…"
        $pruebas = & $Py -m pytest -q 2>&1
        $resumen = $pruebas | Select-String -Pattern "passed|failed" | Select-Object -Last 1
        if ($LASTEXITCODE -ne 0) {
            $fallos += "las pruebas no pasan"
            Write-Host ($pruebas | Select-Object -Last 20) -ForegroundColor DarkGray
        } else {
            Ok "Pruebas: $resumen"
        }
    }

    if ($fallos.Count) {
        Fatal "La instalación terminó pero no quedó operativa: $($fallos -join '; ')"
    }
} finally {
    Pop-Location
}

# --------------------------------------------------------------------------- #
# Resumen
# --------------------------------------------------------------------------- #
$activar = if ($Modo -eq "Venv") { "$Destino\Scripts\Activate.ps1" } else { "(entorno de conda ya activo)" }

Write-Host @"

$('=' * 72)
  LISTO — PTNT-BAL operativo
$('=' * 72)

  Python usado ...... $Python
  Entorno ........... $(if ($Modo -eq 'Venv') { $Destino } else { 'el propio entorno de conda' })
  Código ............ $Repo

  PARA TRABAJAR, abra PowerShell y ejecute:

"@ -ForegroundColor White

if ($Modo -eq "Venv") {
    Write-Host "      & '$activar'" -ForegroundColor Yellow
}
Write-Host @"
      cd '$Repo'

  Y ya puede usar:

      ptnt --help                        ver todos los comandos
      ptnt recursos                      cuánto cabe en este servidor
      python scripts\demo_ciclo_completo.py    el proceso entero con datos ficticios
      ptnt sintetico --clientes 2000     generar datos de prueba
      ptnt analizar                      el análisis comercial
      ptnt dashboard                     el tablero del analista

  SIGUIENTE PASO: conectar sus bases de origen.
  Las credenciales NO van en el YAML — se declaran nombres de variables de
  entorno y el valor se guarda en la máquina:

      setx /M PTNT_ORACLE_USER  "usuario"
      setx /M PTNT_ORACLE_PASS  "contraseña"

  Detalle en docs\INSTALACION_WINDOWS.md y docs\SEGURIDAD.md

"@ -ForegroundColor White

if ($script:Avisos.Count) {
    Write-Host "  Avisos que conviene leer:" -ForegroundColor Yellow
    foreach ($a in $script:Avisos) { Write-Host "    - $a" -ForegroundColor Yellow }
    Write-Host ""
}
