# PTNT-BAL Campo — aplicación Android

Aplicación de campo del sistema PTNT-BAL: el técnico descarga su trabajo, lo hace
**sin señal** y devuelve los cambios al modelo. Inspirada en el flujo de ArcGIS
Field Maps, **sin licencias de ArcGIS**: GeoPackage OGC y MapLibre GL.

La documentación funcional completa está en
[`../docs/APLICACION_MOVIL.md`](../docs/APLICACION_MOVIL.md). Este archivo cubre
solo cómo compilarla y qué esperar del proyecto.

## Compilar

```bash
./gradlew assembleDebug      # APK de pruebas → app/build/outputs/apk/debug/
./gradlew test               # pruebas JVM: proyección UTM y códec de geometría
./gradlew assembleRelease    # APK con R8 y reducción de recursos
```

Requiere JDK 17 y el SDK de Android 35. **No hace falta ninguna clave de API**: la
cartografía viaja dentro del paquete de trabajo que entrega el backend.

## Probar

El paso a paso —simulador sin dispositivo, emulador y teléfono— está en
[`../docs/PRUEBAS_CAMPO_WINDOWS.md`](../docs/PRUEBAS_CAMPO_WINDOWS.md).

Dos cosas que ahorran tiempo la primera vez:

* Desde el emulador, el servidor **no** es `localhost` sino `http://10.0.2.2:8000`.
* Buena parte del ciclo se verifica sin compilar nada:
  `ptnt campo-simular --paquete <archivo>.gpkg` edita el GeoPackage real con las
  mismas reglas que la app.

## Qué hay dentro

| Paquete | Qué resuelve |
|---|---|
| `data/` | Acceso directo al GeoPackage (sin ORM), formularios leídos del manifiesto, ciclo de vida del paquete abierto |
| `domain/` | Edición topológica con snap y propagación; fotos con ubicación, hora y hash |
| `geo/` | Conversión UTM 17S ⇄ WGS84, ubicación del dispositivo, servidor local de teselas |
| `sync/` | Cliente HTTP con el backend y sesión cifrada en el Keystore |
| `work/` | Subida diferida cuando vuelve la señal |
| `ui/` | Pantallas Compose: vinculación, órdenes, mapa y atributos |

## Cuatro decisiones que explican el resto

**minSdk 24 (Android 7).** Cubre el parque de equipos que efectivamente se
entrega a las cuadrillas. Subir el mínimo dejaría fuera teléfonos que están en uso
hoy.

**Sin ORM.** El GeoPackage llega del servidor con su esquema ya definido. Una capa
de mapeo obligaría a declarar las entidades en el código y a publicar una versión
nueva cada vez que el backend agregue un campo — semanas, en una distribuidora.
Leyendo el esquema del propio archivo, la app se adapta sola.

**Sin CameraX.** La cámara del fabricante ya trae enfoque, HDR y estabilización
ajustados a ese sensor. Lo que sí es nuestro —ubicación, hora, autor y hash— se
escribe después, sobre el archivo que devuelve.

**Consulta por ventana.** El mapa pide al GeoPackage solo lo visible, apoyado en
el R\*Tree. Cargar los 30 000 elementos del paquete de una vez agota la memoria de
un equipo de gama baja, que es justamente el que se entrega a campo.

## Pruebas

Las dos piezas donde un error pasa inadvertido están cubiertas por pruebas JVM:

* **`UtmTest`** — la proyección. Un error aquí no rompe nada: solo deja los
  elementos unos metros corridos, el técnico "corrige" posiciones que estaban
  bien y el SIG queda peor que antes de la visita. Error de ida y vuelta medido:
  **< 1 mm**.
* **`GeometriaTest`** — el contrato binario con el backend. Se verificó que Python
  y Kotlin producen el **mismo binario byte a byte** y que cada uno lee lo del
  otro.

Las pruebas instrumentadas en dispositivo —render, permisos, GPS bajo cobertura
real— quedan pendientes: no se pueden hacer sin equipos reales.
