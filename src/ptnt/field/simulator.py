"""Simulador de la jornada de campo, sobre el paquete real.

Ejecuta en Python **las mismas operaciones que hace la aplicación Android** sobre
el mismo GeoPackage: editar atributos, mover con propagación topológica,
reconectar un consumidor, adjuntar fotografías y cerrar la orden. Escribe el
mismo diario de cambios, con la misma numeración y los mismos campos.

Existe por una razón concreta: **el contrato entre el móvil y el backend es la
parte del sistema donde un error no se ve**. Un cambio mal numerado, una
geometría escrita con otra envolvente o un diario sin autor no fallan al
escribirse — fallan semanas después, al sincronizar, cuando el técnico ya no está
en el sitio. Poder recorrer el ciclo completo sin un dispositivo permite probar
ese contrato en cada ejecución de la suite y en la demostración.

No sustituye a las pruebas en dispositivo: no dice nada del render, de los
permisos ni del GPS. Dice que **lo que el móvil escribe, el backend lo entiende**.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ptnt.field.gpkg import GeoPackage, ahora_utc, leer_geometria, linea, punto
from ptnt.field.topology_edit import TipoRelacion, construir_grafo


def _wkt(coords: list[tuple[float, float]]) -> str:
    """WKT del diario: es lo que el supervisor ve al revisar el antes/después."""

    if len(coords) == 1:
        return f"POINT({coords[0][0]} {coords[0][1]})"
    puntos = ", ".join(f"{x} {y}" for x, y in coords)
    return f"LINESTRING({puntos})"


def _linea(coords: list[tuple[float, float]]) -> bytes:
    return linea([(float(x), float(y)) for x, y in coords])


@dataclass
class ResumenJornada:
    """Lo que dejó la jornada, para contrastarlo con lo que llegue al backend."""

    atributos: int = 0
    movimientos: int = 0
    propagados: int = 0
    reconexiones: int = 0
    altas: int = 0
    bajas: int = 0
    fotos: int = 0
    ordenes_cerradas: list[str] = field(default_factory=list)
    ordenes_en_proceso: list[str] = field(default_factory=list)
    advertencias: list[str] = field(default_factory=list)

    @property
    def cambios(self) -> int:
        return (self.atributos + self.movimientos + self.propagados
                + self.reconexiones + self.altas + self.bajas)

    def to_dict(self) -> dict:
        return {
            "cambios": self.cambios, "atributos": self.atributos,
            "movimientos": self.movimientos, "propagados": self.propagados,
            "reconexiones": self.reconexiones, "altas": self.altas,
            "bajas": self.bajas, "fotos": self.fotos,
            "cerradas": len(self.ordenes_cerradas),
            "en_proceso": len(self.ordenes_en_proceso),
        }


class SimuladorCampo:
    """Una jornada de trabajo sobre un paquete descargado."""

    def __init__(self, ruta_gpkg: str | Path, *, usuario: str,
                 orden_trabajo: str = ""):
        self.ruta = Path(ruta_gpkg)
        self.usuario = usuario
        self.orden_trabajo = orden_trabajo
        self.gp = GeoPackage(self.ruta)
        self.resumen = ResumenJornada()
        # Posición del dispositivo. Se guarda con cada cambio porque el diario
        # tiene que decir con qué precisión se trabajó, no solo dónde.
        self.lat: float | None = -2.17
        self.lon: float | None = -79.92
        self.precision_m: float = 6.0

    # -- ciclo de vida -----------------------------------------------------
    def cerrar(self) -> None:
        self.gp.close()

    def __enter__(self) -> "SimuladorCampo":
        return self

    def __exit__(self, *exc) -> None:
        self.cerrar()

    # -- lectura -----------------------------------------------------------
    def elementos(self, capa: str, limite: int | None = None) -> list[dict]:
        try:
            return self.gp.leer(capa, limite=limite)
        except Exception:
            return []

    def ordenes(self) -> list[dict]:
        return self.elementos("ptnt_orden_trabajo")

    def _siguiente_secuencia(self) -> int:
        # Cuenta sobre TODO el diario, incluido lo ya sincronizado: el backend
        # rechaza un diario con huecos, y reiniciar la numeración al subir haría
        # que el lote del día siguiente chocara con el del día anterior.
        r = self.gp.con.execute(
            "SELECT COALESCE(MAX(secuencia), 0) + 1 FROM ptnt_cambio").fetchone()
        return int(r[0])

    def _anotar(self, *, capa: str, elemento_guid: str, operacion: str,
                campo: str = "", valor_antes=None, valor_despues=None,
                geom_antes: str = "", geom_despues: str = "",
                motivo: str = "", propagado_de: str = "") -> None:
        self.gp.insertar("ptnt_cambio", [{
            "guid": str(uuid.uuid4()),
            "secuencia": self._siguiente_secuencia(),
            "capa": capa, "elemento_guid": elemento_guid,
            "operacion": operacion, "campo": campo,
            "valor_antes": None if valor_antes is None else str(valor_antes),
            "valor_despues": None if valor_despues is None else str(valor_despues),
            "geom_antes": geom_antes, "geom_despues": geom_despues,
            "orden_trabajo": self.orden_trabajo,
            "autor": self.usuario, "ocurrido_en": ahora_utc(),
            "lat_dispositivo": self.lat, "lon_dispositivo": self.lon,
            "precision_m": self.precision_m,
            "motivo": motivo, "propagado_de": propagado_de,
            "estado_revision": "PENDIENTE", "sincronizado": 0,
        }])

    # -- edición de atributos ---------------------------------------------
    def editar_atributos(self, capa: str, guid: str, nuevos: dict, *,
                         motivo: str = "") -> int:
        """Edita atributos registrando **un cambio por campo**.

        Granular a propósito: en la revisión, el supervisor puede aceptar la
        lectura del medidor y rechazar el cambio de tarifa del mismo cliente. Un
        único cambio "se editó el cliente" obligaría a decidir por todo junto.
        """

        actuales = self.gp.leer(capa, where=f"guid = '{guid}'", limite=1)
        if not actuales:
            self.resumen.advertencias.append(
                f"No existe {guid} en {capa}: no se editó nada.")
            return 0
        actual = actuales[0]

        cambiados = {k: v for k, v in nuevos.items()
                     if str(actual.get(k)) != str(v)}
        if not cambiados:
            return 0

        self.gp.actualizar(capa, {
            **cambiados,
            "editado_por": self.usuario, "editado_en": ahora_utc(),
            "origen_edicion": "MOVIL",
        }, donde="guid = ?", args=(guid,))

        for campo, valor in cambiados.items():
            self._anotar(capa=capa, elemento_guid=guid, operacion="MODIFICAR",
                         campo=campo, valor_antes=actual.get(campo),
                         valor_despues=valor, motivo=motivo)
        self.resumen.atributos += len(cambiados)
        return len(cambiados)

    # -- movimiento con propagación ---------------------------------------
    def mover(self, capa: str, guid: str, nueva_x: float, nueva_y: float, *,
              motivo: str = "") -> int:
        """Mueve un elemento y arrastra lo que topológicamente debe seguirlo.

        Reutiliza el mismo motor que valida el backend (`topology_edit`), así que
        lo que se prueba aquí es exactamente la regla que se aplicará después.
        """

        grafo = self._grafo()
        if guid not in grafo.elementos:
            self.resumen.advertencias.append(
                f"{guid} no está en el grafo del paquete.")
            return 0

        res = grafo.mover(guid, nueva_x, nueva_y)
        for c in res.cambios:
            # El grafo devuelve la geometría completa ya recolocada, punto o
            # línea. Recalcularla aquí duplicaría la regla de propagación y las
            # dos copias se separarían al primer cambio.
            geom = (punto(*c.coords_despues[0]) if len(c.coords_despues) == 1
                    else _linea(c.coords_despues))
            self.gp.actualizar(c.capa, {
                "geom": geom, "editado_por": self.usuario,
                "editado_en": ahora_utc(), "origen_edicion": "MOVIL",
            }, donde="guid = ?", args=(c.guid,))
            self._anotar(
                capa=c.capa, elemento_guid=c.guid, operacion="MOVER",
                geom_antes=_wkt(c.coords_antes),
                geom_despues=_wkt(c.coords_despues),
                motivo=c.motivo or motivo, propagado_de=c.propagado_de or "")

        self.resumen.movimientos += 1
        self.resumen.propagados += max(0, len(res.cambios) - 1)
        self.resumen.advertencias.extend(res.advertencias)
        return len(res.cambios)

    # -- reconexión --------------------------------------------------------
    def reconectar(self, guid_cliente: str, nuevo_puesto_guid: str, *,
                   capa: str = "ptnt_cliente", motivo: str = "") -> bool:
        """Cambia de qué transformador cuelga un consumidor.

        Es la corrección que más vale de la jornada y la única que altera **dos**
        balances con una sola edición. Se escribe con operación propia,
        ``RECONECTAR``, para que el backend rehaga la topología y las dos zonas
        —no solo el balance del elemento—.
        """

        filas = self.gp.leer(capa, where=f"guid = '{guid_cliente}'", limite=1)
        if not filas:
            self.resumen.advertencias.append(
                f"No existe {guid_cliente} en {capa}.")
            return False
        anterior = str(filas[0].get("puesto_guid") or "")
        if anterior == nuevo_puesto_guid:
            self.resumen.advertencias.append(
                "Reconexión al mismo transformador: se omite.")
            return False
        destino = self.gp.leer("ptnt_puesto_transformacion",
                               where=f"guid = '{nuevo_puesto_guid}'", limite=1)
        if not destino:
            self.resumen.advertencias.append(
                f"El transformador {nuevo_puesto_guid[:8]} no está en el paquete.")
            return False

        self.gp.actualizar(capa, {
            "puesto_guid": nuevo_puesto_guid,
            "editado_por": self.usuario, "editado_en": ahora_utc(),
            "origen_edicion": "MOVIL",
        }, donde="guid = ?", args=(guid_cliente,))

        # El vínculo viejo se borra en el mismo paso que se crea el nuevo: si
        # quedaran los dos, el consumo se contaría en dos zonas a la vez.
        self.gp.con.execute(
            "DELETE FROM ptnt_conexion WHERE tipo_relacion = 'ALIMENTA' "
            "AND ((guid_origen = ? AND guid_destino = ?) "
            "  OR (guid_origen = ? AND guid_destino = ?))",
            (anterior, guid_cliente, guid_cliente, anterior))
        self.gp.con.commit()
        self.gp.insertar("ptnt_conexion", [{
            "guid_origen": nuevo_puesto_guid, "guid_destino": guid_cliente,
            "tipo_relacion": "ALIMENTA",
            "capa_origen": "ptnt_puesto_transformacion", "capa_destino": capa,
        }])

        self._anotar(capa=capa, elemento_guid=guid_cliente,
                     operacion="RECONECTAR", campo="puesto_guid",
                     valor_antes=anterior or None,
                     valor_despues=nuevo_puesto_guid,
                     motivo=motivo or "Reconexión verificada en sitio")
        self.resumen.reconexiones += 1
        return True

    # -- altas y bajas -----------------------------------------------------
    def crear(self, capa: str, atributos: dict, x: float, y: float, *,
              motivo: str = "") -> str:
        guid = str(uuid.uuid4())
        self.gp.insertar(capa, [{
            **atributos, "guid": guid, "geom": punto(x, y),
            "editado_por": self.usuario, "editado_en": ahora_utc(),
            "origen_edicion": "MOVIL",
        }])
        self._anotar(capa=capa, elemento_guid=guid, operacion="CREAR",
                     geom_despues=f"POINT({x} {y})",
                     motivo=motivo or "Elemento nuevo capturado en campo")
        self.resumen.altas += 1
        return guid

    def eliminar(self, capa: str, guid: str, *, motivo: str) -> bool:
        """Elimina si no deja huérfanos. La regla es la del backend."""

        grafo = self._grafo()
        ok, bloqueos = grafo.puede_eliminar(guid)
        if not ok:
            self.resumen.advertencias.extend(bloqueos)
            return False
        filas = self.gp.leer(capa, where=f"guid = '{guid}'", limite=1)
        geom = leer_geometria(filas[0].get("geom")) if filas else None
        self._anotar(capa=capa, elemento_guid=guid, operacion="ELIMINAR",
                     geom_antes=(f"POINT({geom['coords'][0][0]} "
                                 f"{geom['coords'][0][1]})") if geom else "",
                     motivo=motivo)
        self.gp.con.execute(f'DELETE FROM "{capa}" WHERE guid = ?', (guid,))
        self.gp.con.commit()
        self.resumen.bajas += 1
        return True

    # -- fotografías -------------------------------------------------------
    def fotografiar(self, elemento_guid: str, capa_elemento: str, *,
                   descripcion: str = "", con_ubicacion: bool = True) -> str:
        """Adjunta una fotografía con su metadato de ubicación y hora.

        Una foto sin dónde ni cuándo no es evidencia: es una imagen. El hash se
        calcula en la captura para detectar la sustitución del archivo entre el
        campo y la revisión.
        """

        guid = str(uuid.uuid4())
        contenido = f"{guid}{elemento_guid}{ahora_utc()}".encode()
        self.gp.insertar("ptnt_foto", [{
            "guid": guid, "elemento_guid": elemento_guid,
            "capa_elemento": capa_elemento, "orden_trabajo": self.orden_trabajo,
            "archivo": f"fotos/{guid}.jpg",
            "lat": self.lat if con_ubicacion else None,
            "lon": self.lon if con_ubicacion else None,
            "precision_m": self.precision_m if con_ubicacion else None,
            "tomada_en": ahora_utc(), "tomada_por": self.usuario,
            "descripcion": descripcion,
            "hash_sha256": hashlib.sha256(contenido).hexdigest(),
            "bytes": 412_000, "sincronizada": 0,
            "geom": punto(self.lon or 0.0, self.lat or 0.0, srid=4326),
        }])
        self.resumen.fotos += 1
        return guid

    # -- marcado de lo enviado --------------------------------------------
    def marcar_sincronizados(self, lote_id: str) -> int:
        """Marca lo subido, igual que hace el cliente móvil tras el 200.

        Se llama **después** de la respuesta del servidor, nunca antes. Al revés,
        una subida que fallara a mitad dejaría esos cambios marcados como
        enviados y no se reintentarían nunca: se perderían en silencio.

        Sin esto, un trabajo de varios días reenvía cada tarde todo lo anterior y
        el histórico cuenta el mismo cambio tantas veces como días duró la orden.
        """

        cur = self.gp.con.execute(
            "UPDATE ptnt_cambio SET sincronizado = 1, lote_id = ? "
            "WHERE COALESCE(sincronizado, 0) = 0", (lote_id,))
        self.gp.con.execute(
            "UPDATE ptnt_foto SET sincronizada = 1 "
            "WHERE COALESCE(sincronizada, 0) = 0")
        self.gp.con.commit()
        return cur.rowcount

    def pendientes(self) -> int:
        """Cambios que todavía no se han enviado."""

        r = self.gp.con.execute(
            "SELECT COUNT(*) FROM ptnt_cambio "
            "WHERE COALESCE(sincronizado, 0) = 0").fetchone()
        return int(r[0])

    # -- estado de las órdenes --------------------------------------------
    def abrir_orden(self, orden_trabajo: str) -> None:
        """Abrir la orden en el dispositivo la pone EN_PROCESO.

        Es lo que distingue una orden empezada de una que nadie tocó, y lo que el
        backend usa para contar jornadas sin cerrarla.
        """

        self.orden_trabajo = orden_trabajo
        self.gp.actualizar("ptnt_orden_trabajo",
                           {"estado": "EN_PROCESO", "fecha_inicio": ahora_utc()},
                           donde="orden_trabajo = ? AND estado <> 'COMPLETADA'",
                           args=(orden_trabajo,))
        if orden_trabajo not in self.resumen.ordenes_en_proceso:
            self.resumen.ordenes_en_proceso.append(orden_trabajo)

    def cerrar_orden(self, orden_trabajo: str, resultado: str) -> None:
        self.gp.actualizar("ptnt_orden_trabajo", {
            "estado": "COMPLETADA", "resultado": resultado,
            "fecha_cierre": ahora_utc(),
        }, donde="orden_trabajo = ?", args=(orden_trabajo,))
        if orden_trabajo in self.resumen.ordenes_en_proceso:
            self.resumen.ordenes_en_proceso.remove(orden_trabajo)
        self.resumen.ordenes_cerradas.append(orden_trabajo)

    # -- internos ----------------------------------------------------------
    def _grafo(self):
        elementos: list[dict] = []
        for capa in ("ptnt_puesto_transformacion", "ptnt_cliente", "ptnt_poste",
                     "ptnt_tramo", "ptnt_luminaria", "ptnt_seccionador",
                     "ptnt_capacitor"):
            for f in self.elementos(capa):
                g = leer_geometria(f.get("geom"))
                if not g:
                    continue
                elementos.append({
                    "guid": str(f["guid"]), "capa": capa,
                    "coords": g["coords"], "atributos": dict(f),
                })
        conexiones = [
            {"guid_origen": str(c["guid_origen"]),
             "guid_destino": str(c["guid_destino"]),
             "tipo_relacion": str(c.get("tipo_relacion") or "ALIMENTA")}
            for c in self.elementos("ptnt_conexion")
        ]
        return construir_grafo(elementos, conexiones)


__all__ = ["SimuladorCampo", "ResumenJornada", "TipoRelacion"]
