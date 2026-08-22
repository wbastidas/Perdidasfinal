# Instalación paso a paso en Windows Server 2022

Para un servidor con **varios Python instalados** —típicamente el de ArcGIS Pro
más algún otro— donde hay que instalar en uno concreto y no romper los demás.

Escrito para quien no ha hecho esto antes. Si ya sabe, vaya directo al
[atajo](#el-atajo).

---

## El atajo

```powershell
# 1. Permitir ejecutar el script (una sola vez, en esta consola)
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

# 2. Instalar
cd C:\ptnt\Perdidasfinal
.\scripts\instalar_ptnt.ps1 -Python "C:\Users\SU_USUARIO\AppData\Local\ESRI\conda\envs\SU_CLON\python.exe"
```

El script hace todo lo demás y **al final le dice si quedó operativo o no**.
Si algo falla, se detiene con el motivo y qué hacer.

---

## 1. Entender qué Python tiene y cuál va a usar

Este es el punto donde más gente se pierde, así que vamos despacio.

Un servidor con ArcGIS Pro suele tener **tres o más** Python:

| Cuál | Dónde | ¿Instalar aquí? |
|---|---|---|
| **Base de ArcGIS Pro** | `C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3` | **No.** Es de solo lectura y es el que usa Pro para arrancar |
| **Clones de ArcGIS** | `C:\Users\<usuario>\AppData\Local\ESRI\conda\envs\<nombre>` | **Sí.** Para esto existen |
| Python «suelto» | `C:\Python311`, `C:\Users\<u>\AppData\Local\Programs\Python\…` | También sirve, si es 3.11+ |

Para ver **qué clones existen** en su máquina:

```powershell
dir "$env:LOCALAPPDATA\ESRI\conda\envs"
```

Si todavía no tiene un clon, hágalo desde la interfaz —es más fiable que por
consola—: **ArcGIS Pro → Configuración → Administrador de paquetes → junto al
entorno activo, el botón de engranaje → Clonar entorno**. Póngale un nombre que
recuerde, por ejemplo `ptnt`.

La ruta que necesita es la del `python.exe` **dentro** de ese clon:

```
C:\Users\SU_USUARIO\AppData\Local\ESRI\conda\envs\ptnt\python.exe
```

Compruébela antes de seguir:

```powershell
& "C:\Users\SU_USUARIO\AppData\Local\ESRI\conda\envs\ptnt\python.exe" --version
```

Debe responder **Python 3.11** o superior. ArcGIS Pro 3.3 y posteriores traen
3.11; si le sale una versión anterior, ese clon viene de una instalación de Pro
más vieja.

> **¿Por qué un clon y no el base?** El entorno base es el que ArcGIS Pro usa
> para arrancar. Instalar ahí puede dejar Pro sin abrir, y no hay forma cómoda
> de deshacerlo. El clon es desechable: si se estropea, lo vuelve a clonar en
> cinco minutos y no ha perdido nada.

---

## 2. Poner el código en el servidor

```powershell
mkdir C:\ptnt
cd C:\ptnt
git clone https://github.com/wbastidas/Perdidasfinal.git
```

Si el servidor no tiene `git` ni salida a GitHub, descargue el ZIP desde otro
equipo y descomprímalo en `C:\ptnt\Perdidasfinal`. Lo que importa es que exista:

```powershell
dir C:\ptnt\Perdidasfinal\pyproject.toml
```

---

## 3. Ejecutar el instalador

Windows bloquea por defecto los scripts `.ps1`. Se desbloquea **solo para esta
consola**, que es lo mínimo necesario:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Y se instala:

```powershell
cd C:\ptnt\Perdidasfinal
.\scripts\instalar_ptnt.ps1 -Python "C:\Users\SU_USUARIO\AppData\Local\ESRI\conda\envs\ptnt\python.exe"
```

Abra PowerShell **como Administrador** si quiere que el script guarde la clave de
firma de sesiones a nivel de máquina. Si no puede, funciona igual y le dirá cómo
crearla a mano.

### Qué hace, paso a paso

| Paso | Qué hace | Por qué importa |
|---|---|---|
| 1 | Comprueba la versión y qué trae ese entorno | Si es 3.10 o el base de ArcGIS, para aquí en vez de dejar un desastre a medias |
| 2 | Localiza el código | |
| 3 | Crea el entorno de instalación | Ver abajo |
| 4 | Comprueba el acceso a PyPI | Fallar aquí con un mensaje claro ahorra diez minutos de *timeouts* crípticos |
| 5 | **Protege las versiones que ArcGIS fijó** | Ver abajo — es el paso que salva `arcpy` |
| 6 | Instala PTNT-BAL con sus dependencias | |
| 7 | Genera la clave de sesiones y las carpetas | |
| 8 | **Verifica que quedó operativo** | Importa, valida la configuración y corre las 517 pruebas |
| 9 | **Deja la actualización automática** | `ptnt tarea-crear` entrega la línea de `schtasks` para el Programador de Windows (ver [INTERFACES.md](INTERFACES.md) §3) |

Tarda entre 5 y 15 minutos según la red.

---

## 4. Las dos decisiones que el script toma por usted

### Dónde instala: un entorno virtual aparte

Por defecto **no instala dentro del entorno de conda**, sino en un entorno
virtual en `C:\ptnt\env` que se apoya en él.

La diferencia importa:

* El entorno de conda **no se modifica**. Si algo sale mal, borra una carpeta.
* `arcpy`, `numpy` y `pandas` se siguen viendo desde el entorno de ArcGIS, así
  que no se reinstalan ni se pisan sus versiones.
* ArcGIS Pro sigue abriendo igual, pase lo que pase.

**Si le falla la carga de numpy**, el script se detiene y se lo dice: es un
problema conocido de Windows —numpy trae sus DLL en `Library\bin` y esas rutas
las añade la activación de conda, que un entorno virtual no hereda—. La salida
es instalar directamente en el clon, que es seguro porque un clon es desechable:

```powershell
.\scripts\instalar_ptnt.ps1 -Python "C:\...\envs\ptnt\python.exe" -Modo Directo
```

### Qué protege: las versiones de ArcGIS

Este es el riesgo real de instalar sobre un entorno de ArcGIS, y casi nadie lo
avisa: **`pip` actualiza `numpy` o `pandas` para satisfacer una dependencia, y
`arcpy` —compilado contra la versión anterior— deja de importar.**

El script lee qué versiones hay antes de tocar nada y le pasa a `pip` un archivo
de restricciones para que **use las que ya están en vez de actualizarlas**. Si de
verdad no puede resolver la instalación con ellas, **lo dice** en vez de romper
el entorno por su cuenta.

---

## 5. Trabajar día a día

Cada vez que abra una consola nueva:

```powershell
& C:\ptnt\env\Scripts\Activate.ps1     # solo si instaló en modo Venv
cd C:\ptnt\Perdidasfinal
```

Sabrá que está activo porque el prompt empieza con `(env)`.

Y ya:

```powershell
ptnt --help                              # todos los comandos
ptnt recursos                            # cuánto cabe en este servidor
python scripts\demo_ciclo_completo.py    # el proceso entero con datos ficticios
```

Ese último comando es la mejor forma de comprobar que todo funciona antes de
conectar la base real: recorre las 15 etapas y termina con 22 comprobaciones.

---

## 6. Conectar sus bases de origen

**Las credenciales nunca van en el YAML ni en el código.** El sistema declara
*nombres de variables de entorno*, y el valor vive en la máquina:

```powershell
# Como Administrador, ámbito máquina para que el servicio también las vea
setx /M PTNT_ORACLE_USER "usuario_lectura"
setx /M PTNT_ORACLE_PASS "la_contraseña"
setx /M PTNT_ORACLE_DSN  "servidor:1521/ORCL"
```

Y en `config\base.yaml` se referencian **por nombre**:

```yaml
fuentes:
  - nombre: comercial_oracle
    tipo: oracle
    usuario_env: PTNT_ORACLE_USER
    password_env: PTNT_ORACLE_PASS
    dsn_env: PTNT_ORACLE_DSN
```

Cierre y vuelva a abrir PowerShell para que `setx` surta efecto, y compruebe:

```powershell
ptnt probar-fuentes
```

Detalle completo en [`SEGURIDAD.md`](SEGURIDAD.md).

---

## 7. Si algo sale mal

| Lo que ve | Qué es | Qué hacer |
|---|---|---|
| `no se puede cargar el archivo … no está firmado digitalmente` | Windows bloquea scripts | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| `No hay acceso a PyPI` | Servidor sin salida o tras proxy | Añada `-Proxy "http://proxy:8080"`, o instale sin internet (abajo) |
| `El entorno virtual no puede cargar numpy` | Las DLL de conda | Reejecute con `-Modo Directo` |
| `Conflicto de versiones con lo que ArcGIS tiene fijado` | numpy/pandas de ArcGIS demasiado antiguos | Instale menos extras: `-Extras "store,security,test"` |
| `PTNT-BAL necesita Python 3.11` | El clon es de un ArcGIS Pro anterior | Clone desde la versión actual de Pro |
| `Está apuntando al entorno BASE de ArcGIS` | Iba a instalar donde no debe | Use un clon. El script ya lo impidió |
| `No se pudo escribir la variable de máquina` | No es Administrador | Abra PowerShell como Administrador, o cree la variable a mano |

### Instalar sin internet

En un equipo **con** internet y el mismo Windows y la misma versión de Python:

```powershell
pip download "ptnt-bal[all] @ file:///C:/ptnt/Perdidasfinal" -d C:\ruedas
```

Copie `C:\ruedas` al servidor y:

```powershell
.\scripts\instalar_ptnt.ps1 -Python "C:\...\python.exe" -Ruedas C:\ruedas
```

### Empezar de cero

El modo Venv es reversible sin dejar rastro:

```powershell
Remove-Item -Recurse -Force C:\ptnt\env
```

Y vuelva a ejecutar el instalador. En modo Directo, la vuelta atrás es volver a
clonar el entorno desde ArcGIS Pro.

---

## 8. Qué se instala exactamente

El **núcleo** solo necesita `pydantic`, `PyYAML`, `numpy`, `pandas`, `typer`,
`rich` y `loguru`, para que pueda correr en un servidor mínimo. El resto son
extras por capa:

| Extra | Para qué | ¿Imprescindible? |
|---|---|---|
| `store` | Persistencia en DuckDB + Parquet | Sí en la práctica |
| `sources` | SQL Server, PostgreSQL, Oracle, MySQL | Sí, si lee de base |
| `fgdb` | Leer File Geodatabase de ArcGIS sin `arcpy` | Solo si lee `.gdb` |
| `security` | Contraseñas y sesiones | Sí, si publica interfaces |
| `ml` | Detección no supervisada | Recomendable |
| `dashboard` | Tablero del analista | Solo donde lo vaya a abrir |
| `webviewer` | Visor de solo lectura y API móvil | Solo si publica |
| `opendss` | Validación cruzada del flujo | No |
| `test` | Pruebas | Sí, para verificar la instalación |

Para un servidor de proceso sin interfaces:

```powershell
.\scripts\instalar_ptnt.ps1 -Python "C:\...\python.exe" -Extras "store,sources,fgdb,ml,security,test"
```

> **Sobre `arcpy`:** este proyecto **no lo necesita**. Lee File Geodatabase con
> el driver OpenFileGDB de GDAL vía `pyogrio`. Que el entorno sea un clon de
> ArcGIS es una comodidad —tiene Python 3.11 y las librerías científicas ya
> puestas—, no un requisito. Si `pyogrio` no se puede instalar, el resto del
> sistema funciona igual y la alternativa es exportar la geodatabase a GeoPackage
> o CSV desde ArcGIS Pro.

---

Ver también: [Instalación y operación en Windows Server](INSTALACION_WINDOWS.md) ·
[Guía de operación](GUIA_OPERACION.md) · [Seguridad](SEGURIDAD.md) ·
[Probar la app de campo](PRUEBAS_CAMPO_WINDOWS.md)
