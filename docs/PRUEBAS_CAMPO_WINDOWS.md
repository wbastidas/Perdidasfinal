# Probar la aplicación de campo desde Windows

Cómo verificar el ciclo de campo completo en un equipo de oficina, con y sin
teléfono. Paso a paso, con lo que hay que ver en cada uno.

```powershell
# El camino corto: el ciclo entero sin dispositivo, en un minuto
ptnt campo-simular --paquete outputs\campo\paquetes\jperez.gpkg
```

---

## 1. Tres formas de probar, y qué prueba cada una

No son alternativas: cubren cosas distintas y conviene hacerlas en este orden.

| | Qué prueba | Qué **no** prueba | Necesita |
|---|---|---|---|
| **A. Simulador** | El dato: ediciones, subtipos, dominios, snap, diario, sincronización y recálculo | Nada de la interfaz | Solo Python |
| **B. Emulador** | La aplicación real: pantallas, formularios, mapa, flujo | GPS real, cámara real, señal intermitente | Android Studio |
| **C. Teléfono** | Lo que solo pasa en la calle: GPS bajo árboles, sol en la pantalla, batería, red que va y viene | — | Un teléfono y salir |

**El 90 % de lo que se puede romper se rompe en el dato, y eso lo cubre A.** Si
un subtipo no arrastra su dominio o el diario duplica un cambio, el simulador lo
encuentra en segundos y sin cargar un teléfono. Lo que el simulador no puede
decir es si el botón se ve, y eso no se automatiza.

---

## 2. A — El ciclo completo sin dispositivo

Es el que se corre siempre, y el que se puede dejar en el programador de tareas.

### 2.1 Todo de una vez

```powershell
python scripts\demo_ciclo_completo.py
```

Recorre las 15 etapas —clientes sintéticos, balance, focalización, órdenes,
reparto, paquetes, edición en campo, retorno, revisión, recálculo— y termina con
**22 comprobaciones**. Si alguna falla, sale con código distinto de cero y dice
cuál:

```
✓ CICLO COMPLETO COHERENTE — todas las comprobaciones pasaron
```

Sirve tal cual como prueba de humo antes de entregar una versión.

### 2.2 Paso a paso, para ver cada pieza

```powershell
# 1. Datos y análisis
ptnt sintetico --clientes 2000 --meses 36
ptnt analizar

# 2. Decidir el trabajo y repartirlo entre las cuadrillas
ptnt campo-definir --tipo INSPECCION_PNT --alimentador GYE-04 --bloque 40
ptnt campo-repartir --tecnicos jperez,mvera,lcastro

# 3. Armar los paquetes (uno por técnico, con cartografía si se tiene)
ptnt campo-paquetes --teselas cartografia\guayaquil.mbtiles

# 4. Hacer la jornada SIN teléfono
ptnt campo-simular --paquete outputs\campo\paquetes\jperez.gpkg --usuario jperez

# 5. Devolverla y revisarla
ptnt campo-revisar --paquete outputs\campo\paquetes\jperez.gpkg
```

`campo-simular` escribe sobre el **GeoPackage real** con las mismas reglas que la
aplicación: diario de cambios con secuencia, subtipos con sus dominios, snap
topológico, fotos con ubicación y hora. Lo que sale es indistinguible de lo que
subiría un teléfono.

```
                  Jornada simulada — jperez
┏━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Acción                ┃ Detalle                         ┃
┡━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Orden abierta         │ OT-0002                         │
│ Cliente inspeccionado │ 4347cdf3                        │
│ Foto adjunta          │ 8ec6dcbb · con ubicación y hora │
│ Subtipo cambiado      │ BANCO_3 → DELTA_ABIERTO         │
│ Ajuste por subtipo    │ «fases» pasó de ABC a AB        │
└───────────────────────┴─────────────────────────────────┘
```

Esa última línea es la prueba de que el subtipo hizo su trabajo: el banco pasó de
tres unidades a dos y `ABC` —imposible con dos— se convirtió en `AB`, avisando.

### 2.3 Probar varias cuadrillas a la vez

```powershell
python scripts\demo_campo_multiusuario.py
```

Comprueba lo que solo falla con concurrencia: que dos técnicos no reciban la
misma orden, que las descargas y subidas simultáneas no se pisen, y que un
trabajo de varios días no duplique el histórico.

### 2.4 Ver el paquete con los ojos de un SIG

Abra el `.gpkg` con **QGIS** (gratuito). Debe ver las capas, los símbolos y —esto
es lo que conviene verificar— **los desplegables de dominio ya puestos**, porque
los dominios base van en la extensión estándar del formato. Si QGIS los muestra,
cualquier herramienta OGC los va a mostrar.

Los **subtipos** no los entiende QGIS: ningún formato de intercambio los expresa.
Viajan en las tablas `ptnt_subtipo`, `ptnt_subtipo_dominio` y
`ptnt_regla_dominio` del mismo archivo, y quien las lee es la aplicación.

---

## 3. B — La aplicación real en el emulador

Aquí se prueba lo que el simulador no puede: que la pantalla se vea y el flujo se
entienda.

### 3.1 Preparar el equipo (una vez)

1. **JDK 17** — [Adoptium Temurin 17](https://adoptium.net/) (marque «Set
   JAVA_HOME»).
2. **Android Studio** — en el instalador acepte el SDK; después, en
   *Tools → SDK Manager*, instale **Android SDK Platform 35**.
3. En *Tools → Device Manager*, cree un dispositivo: **Pixel 6, API 35**.
   Un teléfono de gama media es la referencia correcta; probar solo en un
   emulador potente esconde justo los problemas de rendimiento que aparecen en
   los equipos que usan las cuadrillas.

Comprobación:

```powershell
java -version                  # debe decir 17
cd mobile
.\gradlew.bat --version
```

### 3.2 Compilar y ejecutar

```powershell
cd mobile
.\gradlew.bat test             # pruebas JVM: proyección, geometría, subtipos
.\gradlew.bat assembleDebug    # APK → app\build\outputs\apk\debug\
```

Con el emulador ya arrancado:

```powershell
.\gradlew.bat installDebug
```

O desde Android Studio: abra la carpeta `mobile`, espere la sincronización de
Gradle y pulse ▶.

### 3.3 Conectar la aplicación con el servidor

El emulador **no** ve `localhost` del equipo anfitrión: para él, el anfitrión es
`10.0.2.2`. Es el error que más tiempo hace perder la primera vez.

```powershell
# En el equipo, deje el servicio corriendo
ptnt campo-servir --host 0.0.0.0 --puerto 8000
```

En la pantalla de vinculación de la aplicación, el servidor es
`http://10.0.2.2:8000`. Con un **teléfono físico en la misma red WiFi**, es la IP
del equipo (`ipconfig` → *Dirección IPv4*), y hay que abrir el puerto:

```powershell
New-NetFirewallRule -DisplayName "PTNT campo" -Direction Inbound `
  -LocalPort 8000 -Protocol TCP -Action Allow -Profile Private
```

### 3.4 Qué mirar, en este orden

1. **Vinculación** — usuario y contraseña; el token queda cifrado en el
   dispositivo.
2. **Órdenes** — debe ver *solo* las suyas. Si ve las de otro técnico, eso es un
   defecto grave: pare y repórtelo.
3. **Descarga** — el paquete baja y el mapa se encuadra solo en su área.
4. **Formulario** — toque un cliente. Compruebe **el subtipo**: cambie la clase
   de servicio y vea que las tarifas del desplegable cambian con ella.
5. **El caso que importa** — abra un puesto de transformación, cambie
   `configuracion_banco` a **Delta abierto** y verifique que:
   - el desplegable de fases pasa a ofrecer solo **AB, BC, CA**;
   - si tenía `ABC`, aparece el aviso «Fases: ABC → AB»;
   - `ABC` **ya no se puede elegir**.
6. **Mover con snap** — mueva un cliente y vea que su acometida lo sigue.
7. **Foto** — tómela y compruebe que queda con coordenada, hora y precisión.
8. **Sin señal** — active el modo avión, siga trabajando, desactívelo: la subida
   diferida debe salir sola.
9. **Sincronizar** — y ver el lote en `ptnt campo-revisar`.

### 3.5 Simular el GPS en el emulador

*Extended controls* (los tres puntos junto al emulador) → **Location**. Ponga una
coordenada dentro del área de trabajo, porque si el dispositivo se cree en
California el mapa se va y no se ve nada. Para Guayaquil: `-2.170998`,
`-79.922359`.

---

## 4. C — En un teléfono, en la calle

Lo que solo se sabe saliendo:

* **El GPS bajo cobertura real.** Entre edificios y árboles la precisión pasa de
  4 m a 30 m, y una edición de geometría con 30 m de error no sirve para corregir
  la posición de un medidor. Por eso la precisión se muestra siempre y el
  servidor advierte por encima de 25 m.
* **La pantalla al sol.** Un contraste que se lee en la oficina no se lee a
  mediodía en la costa.
* **La batería.** Una jornada son ocho horas con el GPS y la pantalla encendidos.
* **La red que va y viene.** No es «con señal o sin señal»: es señal de una barra
  que acepta la conexión y agota el tiempo a la mitad de la subida.

**Estas pruebas no están hechas.** Necesitan un teléfono y una cuadrilla, y son
las únicas que quedan pendientes de todo el ciclo.

---

## 5. Qué hacer cuando algo falla

| Síntoma | Casi siempre es |
|---|---|
| La app no conecta desde el emulador | Se usó `localhost` en vez de `10.0.2.2` |
| No conecta desde el teléfono | El firewall de Windows, o el equipo en red «Pública» |
| El mapa sale vacío | La ubicación simulada está fuera del área, o el paquete no trae teselas |
| El desplegable ofrece de más | El paquete es de una versión anterior del esquema: vuelva a generarlo |
| La sincronización devuelve **503** | Es correcto: hay demasiadas subidas a la vez. El trabajo **ya quedó guardado**; la app reintenta sola |
| La sincronización devuelve **422** con `SYNC20` | Un valor fuera del dominio de su subtipo. El mensaje dice cuál y qué se permite |
| `gradlew` falla al descargar | Proxy corporativo: configure `gradle.properties` con `systemProp.https.proxyHost` |

---

## 6. Qué se prueba solo, en cada cambio

```powershell
python -m pytest -q        # 517 pruebas del backend
cd mobile; .\gradlew.bat test    # pruebas JVM de la aplicación
```

Las pruebas JVM incluyen `SubtipoTest`, que fija **los mismos casos** que el
backend: si el móvil y el servidor dejaran de coincidir en qué valor es válido,
el técnico llenaría el formulario, sincronizaría por la tarde y le rebotarían el
trabajo del día sin entender por qué.

> **Nota honesta:** las pruebas JVM de la aplicación están escritas pero **no se
> han ejecutado** en este entorno, que no tiene Gradle ni el SDK de Android.
> Córralas en el primer equipo con Android Studio.

---

Ver también: [Aplicación móvil](APLICACION_MOVIL.md) ·
[Instalación en Windows](INSTALACION_WINDOWS.md) ·
[Guía de operación](GUIA_OPERACION.md) · [Pruebas](PRUEBAS.md)
