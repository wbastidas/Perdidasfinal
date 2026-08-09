"""Armado del paquete de campo: el GeoPackage que el técnico se lleva.

Un paquete contiene **solo lo que hace falta para las órdenes asignadas**, y ese
recorte es la diferencia entre una app usable y una inservible. Bajar la red
completa de una unidad de negocio a un teléfono de gama baja significa 300 MB, un
minuto de arranque y un mapa que se traba: el técnico deja de usarla a la segunda
salida.

El recorte se hace por **área de trabajo**: la unión de los círculos alrededor de
cada orden asignada, más un margen. Y arrastra el **contexto topológico
necesario**: si un cliente entra al paquete, entra su acometida, su poste y su
transformador aunque el transformador quede fuera del círculo. Sin ese contexto,
el técnico no podría reasignar el cliente a otro transformador ni el motor de
snap tendría a qué pegarse.

Contenido del paquete:

* Las capas de red recortadas al área, con su grafo de conectividad.
* Las órdenes asignadas al usuario.
* Los sectores objetivo del análisis, como referencia.
* La cartografía base **offline** (teselas), si se provee.
* El manifiesto: quién, qué versión de red, qué esquema, qué área.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from ptnt.field.gpkg import (
    GeoPackage,
    SRID_UTM17S,
    ahora_utc,
    linea,
    punto,
)
from ptnt.field.schema import (
    VERSION_ESQUEMA,
    capas_referencia,
    capas_red,
    capas_trabajo,
    esquema_para_movil,
)
from ptnt.field.workorders import Asignacion, EstadoOrden, RegistroCampo


@dataclass
class AreaTrabajo:
    """Área que cubre el paquete: unión de las órdenes más un margen."""

    circulos: list[tuple[float, float, float]] = field(default_factory=list)
    margen_m: float = 250.0

    def contiene(self, x: float, y: float) -> bool:
        for cx, cy, r in self.circulos:
            if math.hypot(x - cx, y - cy) <= r + self.margen_m:
                return True
        return False

    def envolvente(self) -> tuple[float, float, float, float] | None:
        if not self.circulos:
            return None
        xs0 = [cx - r - self.margen_m for cx, _, r in self.circulos]
        ys0 = [cy - r - self.margen_m for _, cy, r in self.circulos]
        xs1 = [cx + r + self.margen_m for cx, _, r in self.circulos]
        ys1 = [cy + r + self.margen_m for _, cy, r in self.circulos]
        return min(xs0), min(ys0), max(xs1), max(ys1)

    @property
    def area_km2(self) -> float:
        env = self.envolvente()
        if not env:
            return 0.0
        return (env[2] - env[0]) * (env[3] - env[1]) / 1e6


@dataclass
class ResultadoPaquete:
    ruta: Path
    usuario: str
    ordenes: int
    elementos_por_capa: dict[str, int] = field(default_factory=dict)
    conexiones: int = 0
    area_km2: float = 0.0
    bytes: int = 0
    teselas: int = 0
    advertencias: list[str] = field(default_factory=list)

    @property
    def mb(self) -> float:
        return self.bytes / 1e6

    def resumen(self) -> dict:
        return {
            "archivo": self.ruta.name, "usuario": self.usuario,
            "ordenes": self.ordenes,
            "elementos": sum(self.elementos_por_capa.values()),
            "conexiones": self.conexiones,
            "area_km2": round(self.area_km2, 2),
            "tamano_mb": round(self.mb, 2), "teselas": self.teselas,
        }


def construir_paquete(
    destino: str | Path,
    *,
    usuario: str,
    asignaciones: list[Asignacion],
    red: dict[str, pd.DataFrame],
    conexiones: pd.DataFrame | None = None,
    sectores: pd.DataFrame | None = None,
    teselas: str | Path | None = None,
    version_red: str = "",
    margen_m: float = 250.0,
    max_elementos: int = 60_000,
) -> ResultadoPaquete:
    """Construye el GeoPackage de campo para un usuario.

    ``red`` mapea nombre de capa → DataFrame con al menos ``guid`` y las columnas
    del esquema; las capas con geometría necesitan ``x``/``y`` (punto) o
    ``coords`` (línea, lista de pares).

    ``max_elementos`` es un tope de seguridad: por encima de ~60 000 elementos el
    render se degrada en dispositivos modestos, que es justamente el equipo que
    se entrega a campo. Si se supera, se avisa en vez de entregar un paquete que
    no se va a poder usar.
    """

    destino = Path(destino)
    res = ResultadoPaquete(ruta=destino, usuario=usuario,
                           ordenes=len(asignaciones))

    # --- 1. Área de trabajo desde las órdenes --------------------------------
    area = AreaTrabajo(margen_m=margen_m)
    for a in asignaciones:
        if a.x is not None and a.y is not None:
            area.circulos.append((a.x, a.y, max(a.radio_m, 50.0)))
    if not area.circulos:
        res.advertencias.append(
            "Ninguna orden trae coordenada: el paquete se arma con TODA la red "
            "disponible. Revise que el plan de focalización tenga geometría.")
    res.area_km2 = area.area_km2

    if destino.exists():
        destino.unlink()

    with GeoPackage(destino) as gp:
        # --- 2. Crear el esquema completo -----------------------------------
        for capa in capas_red() + capas_trabajo() + capas_referencia():
            gp.crear_capa(capa)

        # --- 3. Recortar y cargar la red ------------------------------------
        guids_incluidos: set[str] = set()
        for capa in capas_red():
            df = red.get(capa.nombre)
            if df is None or df.empty:
                res.elementos_por_capa[capa.nombre] = 0
                continue
            filas, incluidos = _filas_capa(df, capa, area, con_area=bool(area.circulos))
            guids_incluidos.update(incluidos)
            gp.insertar(capa.nombre, filas)
            res.elementos_por_capa[capa.nombre] = len(filas)

        total = sum(res.elementos_por_capa.values())
        if total > max_elementos:
            res.advertencias.append(
                f"El paquete tiene {total:,} elementos (tope recomendado "
                f"{max_elementos:,}). En dispositivos de gama baja el mapa se "
                "vuelve lento: considere dividir la jornada en menos órdenes o "
                "reducir el radio.")

        # --- 4. Conectividad, incluyendo el contexto topológico -------------
        if conexiones is not None and not conexiones.empty:
            con = conexiones.copy()
            con["guid_origen"] = con["guid_origen"].astype(str)
            con["guid_destino"] = con["guid_destino"].astype(str)
            if guids_incluidos:
                # Se conservan las conexiones donde AL MENOS un extremo está en el
                # paquete: son las que le dicen al móvil qué hay al otro lado.
                m = (con["guid_origen"].isin(guids_incluidos)
                     | con["guid_destino"].isin(guids_incluidos))
                con = con[m]
            cols = ["guid_origen", "guid_destino", "tipo_relacion",
                    "capa_origen", "capa_destino"]
            gp.insertar("ptnt_conexion",
                        con[[c for c in cols if c in con.columns]]
                        .to_dict("records"))
            res.conexiones = len(con)

        # --- 5. Órdenes de trabajo ------------------------------------------
        filas_ot = []
        for a in asignaciones:
            f = {
                "guid": a.guid, "orden_trabajo": a.orden_trabajo,
                "nivel": a.nivel, "entidad": a.entidad,
                "feeder_code": a.feeder_code, "accion": a.accion,
                "motivo": a.motivo,
                "clientes_a_revisar": a.clientes_a_revisar,
                "recuperable_kwh_mes": a.recuperable_kwh_mes,
                "asignado_a": a.asignado_a, "estado": a.estado.value,
                "fecha_asignacion": a.fecha_asignacion, "radio_m": a.radio_m,
            }
            if a.x is not None and a.y is not None:
                f["geom"] = punto(a.x, a.y)
            filas_ot.append(f)
        gp.insertar("ptnt_orden_trabajo", filas_ot)

        # --- 6. Sectores objetivo (referencia) ------------------------------
        if sectores is not None and not sectores.empty:
            filas_s = []
            for _, r in sectores.iterrows():
                x, y = _xy(r)
                if x is None or (area.circulos and not area.contiene(x, y)):
                    continue
                filas_s.append({
                    "geom": punto(x, y),
                    "sector_id": str(r.get("entidad") or r.get("sector_id", "")),
                    "prioridad": _num(r.get("prioridad")),
                    "clientes": int(r.get("clientes", 0) or 0),
                    "recuperable_kwh_mes": _num(r.get("recuperable_kwh_mes")),
                    "radio_m": _num(r.get("radio_m")),
                    "motivo": str(r.get("razon_1") or r.get("motivo", "")),
                })
            gp.insertar("ptnt_sector_objetivo", filas_s)

        # --- 7. Índices espaciales y envolventes ----------------------------
        for capa in capas_red() + capas_trabajo() + capas_referencia():
            if capa.tiene_geometria:
                gp.reindexar(capa.nombre)
                gp.actualizar_extension(capa.nombre)

        # --- 8. Cartografía base offline ------------------------------------
        if teselas:
            res.teselas = _adjuntar_teselas(gp, Path(teselas), area)
            if res.teselas == 0:
                res.advertencias.append(
                    "No se adjuntaron teselas: el técnico verá la red sobre fondo "
                    "vacío. Provea un MBTiles o una caché de ArcGIS Server.")

        # --- 9. Manifiesto ---------------------------------------------------
        env = area.envolvente()
        gp.escribir_manifiesto({
            "version_esquema": VERSION_ESQUEMA,
            "version_red": version_red,
            "usuario": usuario,
            "generado_en": ahora_utc(),
            "srid": SRID_UTM17S,
            "ordenes": [a.orden_trabajo for a in asignaciones],
            "envolvente": list(env) if env else None,
            "area_km2": round(res.area_km2, 2),
            "elementos_por_capa": res.elementos_por_capa,
            "esquema": esquema_para_movil(),
            "paquete_id": str(uuid.uuid4()),
        })

    res.bytes = destino.stat().st_size
    return res


def _filas_capa(df: pd.DataFrame, capa, area: AreaTrabajo, *, con_area: bool
                ) -> tuple[list[dict], set[str]]:
    """Convierte un DataFrame a filas del GeoPackage, recortando al área."""

    nombres = {c.nombre for c in capa.campos}
    filas: list[dict] = []
    incluidos: set[str] = set()

    for _, r in df.iterrows():
        f: dict = {}
        geom = None
        if capa.tipo_geometria == "POINT":
            x, y = _xy(r)
            if x is None:
                continue
            if con_area and not area.contiene(x, y):
                continue
            geom = punto(x, y)
        elif capa.tipo_geometria == "LINESTRING":
            coords = r.get("coords")
            if not isinstance(coords, (list, tuple)) or len(coords) < 2:
                continue
            coords = [(float(a), float(b)) for a, b in coords]
            if con_area and not any(area.contiene(a, b) for a, b in coords):
                continue
            geom = linea(coords)

        if geom is not None:
            f["geom"] = geom
        for k in nombres:
            if k in r.index:
                v = r[k]
                f[k] = None if pd.isna(v) else (
                    bool(v) if isinstance(v, (bool,)) else v)
        if "guid" in f and f["guid"]:
            incluidos.add(str(f["guid"]))
        filas.append(f)
    return filas, incluidos


def _adjuntar_teselas(gp: GeoPackage, origen: Path, area: AreaTrabajo) -> int:
    """Copia teselas de un MBTiles a la tabla de teselas del GeoPackage.

    Se acepta MBTiles porque es lo que produce cualquier cadena libre (QGIS,
    tippecanoe, un servidor WMTS descargado) y también lo que exporta una caché de
    ArcGIS Server. Así el requisito de "cartografía open source o de ArcGIS
    Server" se cumple sin licencias: el formato de entrada es el mismo.
    """

    if not origen.exists():
        return 0
    import sqlite3

    gp.con.executescript("""
    CREATE TABLE IF NOT EXISTS gpkg_tile_matrix_set (
        table_name TEXT PRIMARY KEY, srs_id INTEGER NOT NULL,
        min_x DOUBLE NOT NULL, min_y DOUBLE NOT NULL,
        max_x DOUBLE NOT NULL, max_y DOUBLE NOT NULL);
    CREATE TABLE IF NOT EXISTS gpkg_tile_matrix (
        table_name TEXT NOT NULL, zoom_level INTEGER NOT NULL,
        matrix_width INTEGER NOT NULL, matrix_height INTEGER NOT NULL,
        tile_width INTEGER NOT NULL, tile_height INTEGER NOT NULL,
        pixel_x_size DOUBLE NOT NULL, pixel_y_size DOUBLE NOT NULL,
        CONSTRAINT pk_ttm PRIMARY KEY (table_name, zoom_level));
    CREATE TABLE IF NOT EXISTS cartografia (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        zoom_level INTEGER NOT NULL, tile_column INTEGER NOT NULL,
        tile_row INTEGER NOT NULL, tile_data BLOB NOT NULL,
        UNIQUE (zoom_level, tile_column, tile_row));
    """)

    src = sqlite3.connect(origen)
    src.row_factory = sqlite3.Row
    n = 0
    try:
        filas = src.execute(
            "SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles")
        lote = []
        for t in filas:
            lote.append((t["zoom_level"], t["tile_column"], t["tile_row"],
                         t["tile_data"]))
            if len(lote) >= 500:
                gp.con.executemany(
                    "INSERT OR REPLACE INTO cartografia "
                    "(zoom_level, tile_column, tile_row, tile_data) VALUES (?,?,?,?)",
                    lote)
                n += len(lote)
                lote = []
        if lote:
            gp.con.executemany(
                "INSERT OR REPLACE INTO cartografia "
                "(zoom_level, tile_column, tile_row, tile_data) VALUES (?,?,?,?)",
                lote)
            n += len(lote)
    except sqlite3.OperationalError:
        return 0
    finally:
        src.close()

    if n:
        env = area.envolvente() or (0, 0, 0, 0)
        gp.con.execute(
            "INSERT OR REPLACE INTO gpkg_tile_matrix_set VALUES (?,?,?,?,?,?)",
            ("cartografia", SRID_UTM17S, *env))
        gp.con.execute(
            "INSERT OR REPLACE INTO gpkg_contents "
            "(table_name, data_type, identifier, description, srs_id) "
            "VALUES (?,?,?,?,?)",
            ("cartografia", "tiles", "cartografia",
             "Cartografía base offline", SRID_UTM17S))
        gp.con.commit()
    return n


def huella_paquete(ruta: str | Path) -> str:
    """Hash del paquete entregado.

    Permite verificar en la sincronización que los cambios vienen del paquete que
    se entregó y no de una copia editada por fuera con otra herramienta."""

    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def _xy(r) -> tuple[float | None, float | None]:
    for cx, cy in (("x", "y"), ("centroid_x", "centroid_y"), ("este", "norte")):
        if cx in r.index and cy in r.index:
            try:
                x, y = float(r[cx]), float(r[cy])
                if not (pd.isna(x) or pd.isna(y)):
                    return x, y
            except (TypeError, ValueError):
                continue
    return None, None


def _num(v) -> float:
    try:
        x = float(v)
        return 0.0 if pd.isna(x) else x
    except (TypeError, ValueError):
        return 0.0
