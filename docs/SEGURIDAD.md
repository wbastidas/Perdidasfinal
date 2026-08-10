# Seguridad — PTNT-BAL

El sistema maneja datos comerciales sensibles (consumo, identificación de
clientes) y produce un ranking de sospecha de hurto de alto impacto reputacional.
La seguridad se diseña en profundidad.

## 1. Secretos y credenciales

**Regla dura: ninguna credencial se guarda en el YAML ni en el código.**

- El YAML solo declara **el nombre de la variable de entorno** que contiene la
  credencial (`usuario_env`, `password_env`, `dsn_env`).
- `ptnt.security.secrets.resolve_source_credentials` las resuelve en tiempo de
  ejecución. Si falta una, el arranque falla nombrando la variable, nunca su valor.
- El objeto de credenciales tiene un `__repr__` que enmascara la contraseña, para
  que no se filtre en logs o trazas.

**Prueba automatizada** (`tests/security`): `test_config_repo_sin_credenciales_embebidas`
falla la CI si aparece `password:` (u otros patrones) en `config/base.yaml`.

### Endurecimiento en Windows (opcional)

En lugar de variables de entorno se puede usar **Windows Credential Manager** o
**DPAPI**. Patrón sugerido: un pequeño arranque que lea el secreto de DPAPI y lo
inyecte como variable de entorno del proceso antes de invocar `ptnt`.

## 2. Autenticación de las interfaces

- Las contraseñas de usuario se almacenan **solo como hash**: bcrypt si el extra
  `security` está instalado; si no, PBKDF2-HMAC-SHA256 (240 000 iteraciones) de la
  librería estándar. En ningún caso texto claro.
- `UserStore` escribe el archivo de usuarios con permisos restrictivos (0600 en
  POSIX; en Windows, restringir por ACL la carpeta `config`).
- La verificación es de **tiempo constante** (`hmac.compare_digest`) y hace una
  verificación *dummy* para usuarios inexistentes, de modo que el tiempo de
  respuesta no revele si un usuario existe.
- Roles: `viewer` (visor), `analyst` (tablero), `admin`. El tablero exige rol
  `analyst`/`admin`.

## 3. Visor web de solo lectura

- **Sin endpoints de escritura.** El visor solo expone `GET`.
- **Autenticación básica** obligatoria (salvo que se desactive explícitamente).
- **Restricción por red (CIDR):** si `seguridad.redes_permitidas` está poblado,
  toda petición desde una IP fuera de esos rangos recibe `403`, además del `401`
  por credenciales. Útil para limitar el acceso a la red corporativa.
- Escucha por defecto en `127.0.0.1`; exponerlo requiere cambio explícito de
  `host` y regla de firewall.

**Pruebas** (`tests/security`): 401 sin credenciales, 401 con credencial errónea,
200 con credencial válida, 403 por red no autorizada.

## 4. Inyección SQL

Los conectores SQL construyen la URL de conexión con `sqlalchemy.URL.create`
(parametrizado), no por concatenación de cadenas con la contraseña. Las consultas
de lectura por tabla usan identificadores controlados por la configuración, no
entrada de usuario final. El visor **no** acepta SQL del cliente; solo sirve
resultados precalculados.

## 5. Transporte (TLS)

- Las fuentes SQL activan cifrado por defecto (`requiere_ssl: true`): `Encrypt=yes`
  en SQL Server, `sslmode=require` en PostgreSQL, `ssl=true` en MySQL.
- Para exponer las interfaces web fuera del host, colocar un **reverse proxy con
  TLS** (IIS con ARR, o nginx) delante; el proceso Python sirve en `localhost` y
  el proxy termina HTTPS.

## 6. Datos en reposo

- DuckDB y Parquet residen en disco local. Proteger la carpeta `data/` y
  `outputs/` con ACL restrictivas (solo la cuenta de servicio).
- Considerar **BitLocker** en el volumen de datos del servidor.
- Política de retención (§5.4 de la especificación): `bronze` N versiones,
  `silver` vigente + anterior, `gold` íntegro, `meta` nunca se purga.

## 7. Auditoría

- Cada corrida queda registrada en `meta.run` con `started_at`, `finished_at`,
  `status`, `config_hash` y snapshot de configuración: trazabilidad completa de
  qué se ejecutó, cuándo y con qué parámetros.
- El ranking de sospecha lleva la evidencia de cada señal, de modo que toda
  clasificación de un cliente como sospechoso es **explicable y reproducible** —
  requisito ético y legal antes de enviar una cuadrilla a inspeccionar.

## 8. Consideración ética / de falsos positivos

Una zona con sospecha alta pero baja confiabilidad de datos es primero un problema
de datos, no de hurto (§10.7 de la especificación). El sistema reporta la
evidencia y las razones para que la decisión humana no se base en un número opaco.
No debe usarse para acción sancionatoria automática.

## 9. Lista de verificación de despliegue

- [ ] Variables de entorno de credenciales definidas a nivel `Machine`/servicio.
- [ ] `PTNT_JWT_SECRET` con clave larga aleatoria.
- [ ] Usuarios creados con `ptnt crear-usuario`; ninguna contraseña por defecto.
- [ ] `config/usuarios.json` con ACL restrictiva.
- [ ] `seguridad.redes_permitidas` configurado si el visor se expone a la red.
- [ ] Reverse proxy TLS delante de las interfaces si salen del host.
- [ ] Carpetas `data/` y `outputs/` con ACL de solo la cuenta de servicio.
- [ ] `pytest -m security` en verde en la CI.
