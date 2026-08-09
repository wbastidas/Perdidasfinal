"""API de sincronización para la aplicación móvil (FastAPI).

Superficie deliberadamente pequeña: cinco endpoints. Cada uno que se agrega es
una versión más de la app que hay que mantener compatible, y publicar una
actualización en una distribuidora puede tardar semanas.

    POST /movil/vincular      teléfono ↔ usuario, emite el token del dispositivo
    GET  /movil/ordenes       órdenes asignadas al usuario
    GET  /movil/paquete       descarga el .gpkg de trabajo
    POST /movil/sincronizar   sube el paquete de retorno con los cambios
    GET  /movil/estado        salud del servicio y versión del esquema

**Autenticación por token de dispositivo**, no por usuario y contraseña en cada
llamada: el técnico se autentica una vez al vincular, y el token viaja después.
Si el teléfono se pierde, se revoca el token desde el backend y ese equipo deja
de sincronizar sin tocar la cuenta del técnico.

El endpoint de sincronización **no aplica nada al modelo**: recibe, valida y deja
el lote en revisión. Aceptar ediciones de red sin revisión humana degradaría el
SIG en vez de mejorarlo.
"""

# Sin `from __future__ import annotations` **a propósito**. Con las anotaciones
# aplazadas, FastAPI recibe `UploadFile` como una cadena y no puede resolverla:
# las importaciones de FastAPI son perezosas —para no obligar a instalarla en la
# máquina que solo calcula— y ocurren dentro de `crear_app`. El resultado era un
# error 500 en `/movil/sincronizar` que no aparecía hasta subir un paquete real.
# El proyecto exige Python 3.11, así que `str | Path` funciona sin el import.

import shutil
import uuid
from pathlib import Path

from ptnt.field.schema import VERSION_ESQUEMA


def crear_app(
    *,
    registro_ruta: str | Path = "outputs/campo/registro.json",
    paquetes_dir: str | Path = "outputs/campo/paquetes",
    entrantes_dir: str | Path = "outputs/campo/entrantes",
    lotes_dir: str | Path = "outputs/campo/lotes",
):
    """Construye la aplicación FastAPI de sincronización móvil."""

    from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
    from fastapi.responses import FileResponse, JSONResponse

    from ptnt.field.sync import recibir_paquete
    from ptnt.field.workorders import EstadoOrden, RegistroCampo

    registro_ruta = Path(registro_ruta)
    paquetes_dir = Path(paquetes_dir)
    entrantes_dir = Path(entrantes_dir)
    lotes_dir = Path(lotes_dir)
    for d in (paquetes_dir, entrantes_dir, lotes_dir):
        d.mkdir(parents=True, exist_ok=True)

    app = FastAPI(
        title="PTNT-BAL — Sincronización móvil",
        version=VERSION_ESQUEMA,
        description="Descarga de trabajo y retorno de ediciones de campo.",
    )

    def _registro() -> RegistroCampo:
        return RegistroCampo(registro_ruta)

    def usuario_actual(authorization: str = Header(default="")):
        """Resuelve el usuario desde el token del dispositivo."""

        token = authorization.removeprefix("Bearer ").strip()
        u = _registro().autenticar_token(token)
        if u is None:
            # Mensaje genérico a propósito: distinguir "token inválido" de
            # "usuario inactivo" le diría a un atacante qué tokens existen.
            raise HTTPException(status_code=401, detail="No autorizado")
        return u

    # -- vinculación -------------------------------------------------------
    @app.post("/movil/vincular")
    def vincular(datos: dict):
        """Vincula un dispositivo con usuario y contraseña, y emite su token."""

        from ptnt.security.auth import verify_password

        usuario = str(datos.get("usuario", ""))
        password = str(datos.get("password", ""))
        dispositivo = str(datos.get("dispositivo_id", ""))
        if not (usuario and password and dispositivo):
            raise HTTPException(
                status_code=400,
                detail="Se requieren usuario, password y dispositivo_id")

        reg = _registro()
        u = reg.usuarios.get(usuario)
        if u is None or not u.activo or not verify_password(
                password, u.password_hash):
            raise HTTPException(status_code=401, detail="Credenciales inválidas")

        token = reg.vincular_dispositivo(usuario, dispositivo)
        reg.save()
        return {
            "token": token, "usuario": u.usuario, "nombre": u.nombre,
            "rol": u.rol.value, "version_esquema": VERSION_ESQUEMA,
        }

    # -- órdenes -----------------------------------------------------------
    @app.get("/movil/ordenes")
    def ordenes(u=Depends(usuario_actual)):
        """Órdenes asignadas al usuario que aún no se sincronizaron."""

        reg = _registro()
        pendientes = reg.de_usuario(u.usuario, estados={
            EstadoOrden.ASIGNADA, EstadoOrden.DESCARGADA, EstadoOrden.EN_PROCESO,
        })
        return {
            "usuario": u.usuario,
            "ordenes": [a.to_dict() for a in pendientes],
            "total": len(pendientes),
        }

    # -- descarga del paquete ---------------------------------------------
    @app.get("/movil/paquete")
    def paquete(u=Depends(usuario_actual)):
        """Entrega el GeoPackage de trabajo del usuario.

        El paquete lo genera el backend por adelantado (`ptnt campo-paquete`), no
        al vuelo: armarlo puede tardar y el técnico suele descargarlo con la
        conexión justa antes de salir.
        """

        ruta = paquetes_dir / f"{u.usuario}.gpkg"
        if not ruta.exists():
            raise HTTPException(
                status_code=404,
                detail=("Aún no hay paquete generado para este usuario. "
                        "El supervisor debe asignarle órdenes y generar el "
                        "paquete."))

        # Transición en lote y en una sola transacción: si dos técnicos bajan su
        # paquete a la vez, cada uno mueve solo sus órdenes y ninguno pisa al otro.
        _registro().marcar_descargadas(u.usuario, actor=u.usuario)

        return FileResponse(
            ruta, media_type="application/geopackage+sqlite3",
            filename=f"trabajo_{u.usuario}.gpkg")

    # -- sincronización ----------------------------------------------------
    @app.post("/movil/sincronizar")
    async def sincronizar(archivo: UploadFile = File(...),
                          u=Depends(usuario_actual)):
        """Recibe el paquete de retorno, lo valida y lo deja **en revisión**."""

        destino = entrantes_dir / f"{u.usuario}_{uuid.uuid4().hex[:8]}.gpkg"
        with destino.open("wb") as f:
            shutil.copyfileobj(archivo.file, f)

        lote = recibir_paquete(destino, usuario_esperado=u.usuario)

        import json
        (lotes_dir / f"{lote.lote_id}.json").write_text(
            json.dumps({
                "lote_id": lote.lote_id, "usuario": lote.usuario,
                "paquete_id": lote.paquete_id, "recibido_en": lote.recibido_en,
                "archivo": str(destino),
                "cambios": [c.to_dict() for c in lote.cambios],
                "fotos": lote.fotos, "ordenes": lote.ordenes,
                "hallazgos": [
                    {"severidad": h.severidad.value, "codigo": h.codigo,
                     "detalle": h.detalle, "elemento_guid": h.elemento_guid}
                    for h in lote.hallazgos],
            }, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")

        cerradas, en_curso = [], []
        if not lote.bloqueado:
            reg = _registro()
            # **Solo** se cierran las órdenes que el técnico marcó COMPLETADA en
            # el dispositivo. Cerrar todas las del paquete —como se hacía— rompe
            # el trabajo de varios días: la sincronización del primer día daría
            # por terminadas órdenes que ni siquiera se han empezado, y el
            # supervisor vería una jornada completa donde hay media mañana.
            for o in lote.ordenes:
                ot = str(o.get("orden_trabajo", ""))
                if not ot:
                    continue
                estado_campo = str(o.get("estado", "")).upper()
                if estado_campo == "COMPLETADA":
                    # Devuelve None si ya estaba cerrada: un reenvío del mismo
                    # paquete no debe fallar ni contar dos veces.
                    if reg.cerrar_orden(ot, resultado=str(o.get("resultado", "")),
                                        actor=u.usuario) is not None:
                        cerradas.append(ot)
                elif estado_campo == "EN_PROCESO":
                    # Solo cuenta jornada lo que el técnico **abrió** en el
                    # dispositivo. Las órdenes que siguen ASIGNADA o DESCARGADA
                    # no se tocaron: anotarles avance inflaría el indicador y
                    # haría parecer trabajada una orden que nadie visitó.
                    if reg.anotar_avance(ot, actor=u.usuario):
                        en_curso.append(ot)

        return JSONResponse({
            "recibido": True,
            "lote_id": lote.lote_id,
            "resumen": lote.resumen(),
            "ordenes_cerradas": cerradas,
            "ordenes_en_curso": en_curso,
            "hallazgos": [
                {"severidad": h.severidad.value, "codigo": h.codigo,
                 "detalle": h.detalle} for h in lote.hallazgos],
            "mensaje": (
                _mensaje_sync(lote, cerradas, en_curso)
                if not lote.bloqueado else
                "El paquete tiene problemas que impiden procesarlo. "
                "Revise los hallazgos y vuelva a sincronizar."),
        }, status_code=200 if not lote.bloqueado else 422)

    def _mensaje_sync(lote, cerradas: list[str], en_curso: list[str]) -> str:
        partes = []
        if lote.cambios:
            partes.append(f"{len(lote.cambios)} cambio(s) recibidos y "
                          "pendientes de revisión del supervisor.")
        if cerradas:
            partes.append(f"{len(cerradas)} orden(es) cerradas.")
        if en_curso:
            # Decirlo importa: el técnico tiene que saber que puede apagar el
            # teléfono sin perder nada y seguir mañana donde lo dejó.
            partes.append(
                f"{len(en_curso)} orden(es) siguen abiertas: el avance quedó "
                "guardado y puede continuar mañana.")
        if not partes:
            partes.append("Sin novedades nuevas que enviar.")
        return " ".join(partes)

    # -- estado ------------------------------------------------------------
    @app.get("/movil/estado")
    def estado():
        return {
            "servicio": "PTNT-BAL sincronización móvil",
            "version_esquema": VERSION_ESQUEMA,
            "ok": True,
        }

    return app
