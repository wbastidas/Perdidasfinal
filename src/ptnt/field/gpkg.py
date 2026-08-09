"""Escritor de **GeoPackage** OGC 1.3 nativo, sin GDAL.

Se escribe directamente sobre `sqlite3` en vez de usar GDAL/fiona por tres
razones que pesan en este proyecto:

1. **El servidor de la distribuidora no siempre puede instalar GDAL.** Es una
   dependencia binaria pesada, con versiones que chocan con las de ArcGIS ya
   instalado en la misma máquina.
2. **Control total del esquema.** El modelo de datos no es genérico: tiene la
   jerarquía Puesto→Unidad, las relaciones por ``CIRCUITSOURCEGUID`` y campos de
   dominio propios. Escribir el GeoPackage a mano permite emitir exactamente las
   tablas, índices y extensiones que el móvil necesita, y ninguna más.
3. **Tamaño del archivo.** Lo que se lleva a campo debe caber en un teléfono de
   gama baja con poca memoria. Cada índice y cada tabla de más se paga en
   milisegundos de arranque y en batería.

GeoPackage es un **SQLite con reglas**: tablas de metadatos (`gpkg_contents`,
`gpkg_spatial_ref_sys`, `gpkg_geometry_columns`), geometrías en un binario propio
(cabecera ``GP`` + WKB estándar) e índices espaciales R*Tree. Todo eso está
implementado aquí; lo que produce este módulo abre sin problemas en QGIS, en
ArcGIS y en cualquier lector OGC.
"""

from __future__ import annotations

import json
import sqlite3
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Application ID y user_version del formato (OGC GeoPackage 1.3)
_APPLICATION_ID = 0x47504B47      # "GPKG"
_USER_VERSION = 10300

SRID_UTM17S = 32717               # EPSG:32717 — WGS 84 / UTM zona 17S
SRID_WGS84 = 4326


# --------------------------------------------------------------------------- #
# Geometría: binario GeoPackage = cabecera + WKB
# --------------------------------------------------------------------------- #
def _wkb_point(x: float, y: float) -> bytes:
    # byte order (1 = little endian) + tipo 1 (Point) + coords
    return struct.pack("<BIdd", 1, 1, float(x), float(y))


def _wkb_linestring(puntos: list[tuple[float, float]]) -> bytes:
    b = struct.pack("<BII", 1, 2, len(puntos))
    for x, y in puntos:
        b += struct.pack("<dd", float(x), float(y))
    return b


def _wkb_polygon(anillo: list[tuple[float, float]]) -> bytes:
    b = struct.pack("<BIII", 1, 3, 1, len(anillo))
    for x, y in anillo:
        b += struct.pack("<dd", float(x), float(y))
    return b


def gpkg_geometry(wkb: bytes, srid: int, envelope: tuple | None = None) -> bytes:
    """Envuelve un WKB en el binario GeoPackage.

    Cabecera: magic ``GP``, versión, flags, SRID y (opcional) envolvente. La
    envolvente se incluye siempre que se conoce: **el móvil la usa para descartar
    geometrías fuera de la vista sin decodificar el WKB**, que es la diferencia
    entre un mapa fluido y uno que se traba en un dispositivo modesto.
    """

    flags = 0b0000_0001                      # little endian
    if envelope is not None:
        flags |= 0b0000_0010                 # envolvente XY presente
    cabecera = b"GP" + bytes([0, flags]) + struct.pack("<i", srid)
    if envelope is not None:
        minx, miny, maxx, maxy = envelope
        cabecera += struct.pack("<dddd", minx, maxx, miny, maxy)
    return cabecera + wkb


def punto(x: float, y: float, srid: int = SRID_UTM17S) -> bytes:
    return gpkg_geometry(_wkb_point(x, y), srid, (x, y, x, y))


def linea(puntos: list[tuple[float, float]], srid: int = SRID_UTM17S) -> bytes:
    xs = [p[0] for p in puntos]
    ys = [p[1] for p in puntos]
    return gpkg_geometry(_wkb_linestring(puntos), srid,
                         (min(xs), min(ys), max(xs), max(ys)))


def poligono(anillo: list[tuple[float, float]], srid: int = SRID_UTM17S) -> bytes:
    xs = [p[0] for p in anillo]
    ys = [p[1] for p in anillo]
    return gpkg_geometry(_wkb_polygon(anillo), srid,
                         (min(xs), min(ys), max(xs), max(ys)))


def leer_geometria(blob: bytes) -> dict | None:
    """Decodifica un binario GeoPackage a ``{"tipo", "coords", "srid"}``.

    Solo soporta punto y línea, que es todo lo que este modelo necesita: los
    elementos de red son postes/puestos (punto) y tramos (línea).
    """

    if not blob or blob[:2] != b"GP":
        return None
    flags = blob[3]
    srid = struct.unpack("<i", blob[4:8])[0]
    ind_env = (flags >> 1) & 0b111
    tam_env = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get(ind_env, 0)
    wkb = blob[8 + tam_env:]
    tipo = struct.unpack("<I", wkb[1:5])[0]
    if tipo == 1:
        x, y = struct.unpack("<dd", wkb[5:21])
        return {"tipo": "Point", "coords": [(x, y)], "srid": srid}
    if tipo == 2:
        n = struct.unpack("<I", wkb[5:9])[0]
        coords = [struct.unpack("<dd", wkb[9 + i * 16: 25 + i * 16])
                  for i in range(n)]
        return {"tipo": "LineString", "coords": coords, "srid": srid}
    return None


# --------------------------------------------------------------------------- #
# Definición de capas
# --------------------------------------------------------------------------- #
@dataclass
class Campo:
    """Un campo de una capa, con su dominio si lo tiene.

    ``dominio`` alimenta el desplegable del formulario en el móvil: escribir a
    mano el código de una estructura en un teclado táctil, bajo sol y con guantes,
    es la principal fuente de error de captura en campo.
    """

    nombre: str
    tipo: str = "TEXT"                 # TEXT | INTEGER | REAL | BOOLEAN | DATETIME
    obligatorio: bool = False
    editable: bool = True
    etiqueta: str = ""
    dominio: list[str] | None = None
    ayuda: str = ""

    def sql(self) -> str:
        return f'"{self.nombre}" {self.tipo}' + (
            " NOT NULL" if self.obligatorio else "")


@dataclass
class Capa:
    """Una capa del paquete de campo."""

    nombre: str
    tipo_geometria: str                # POINT | LINESTRING | POLYGON | NONE
    campos: list[Campo] = field(default_factory=list)
    descripcion: str = ""
    srid: int = SRID_UTM17S
    # Capas de solo consulta (cartografía, referencia) que el móvil no edita.
    editable: bool = True

    @property
    def tiene_geometria(self) -> bool:
        return self.tipo_geometria != "NONE"


class GeoPackage:
    """Escritor/lector de GeoPackage con el esquema del proyecto."""

    def __init__(self, ruta: str | Path):
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.ruta)
        self.con.row_factory = sqlite3.Row
        self._inicializar()

    # -- ciclo de vida -----------------------------------------------------
    def _inicializar(self) -> None:
        c = self.con
        c.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
        c.execute(f"PRAGMA user_version = {_USER_VERSION}")
        # WAL: el móvil escribe mientras lee el mapa; sin WAL cada edición
        # bloquea el render y la app se siente trabada.
        c.execute("PRAGMA journal_mode = WAL")
        c.execute("PRAGMA synchronous = NORMAL")

        c.executescript("""
        CREATE TABLE IF NOT EXISTS gpkg_spatial_ref_sys (
            srs_name TEXT NOT NULL, srs_id INTEGER PRIMARY KEY,
            organization TEXT NOT NULL, organization_coordsys_id INTEGER NOT NULL,
            definition TEXT NOT NULL, description TEXT);

        CREATE TABLE IF NOT EXISTS gpkg_contents (
            table_name TEXT PRIMARY KEY, data_type TEXT NOT NULL,
            identifier TEXT UNIQUE, description TEXT DEFAULT '',
            last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE,
            srs_id INTEGER REFERENCES gpkg_spatial_ref_sys(srs_id));

        CREATE TABLE IF NOT EXISTS gpkg_geometry_columns (
            table_name TEXT NOT NULL, column_name TEXT NOT NULL,
            geometry_type_name TEXT NOT NULL, srs_id INTEGER NOT NULL,
            z TINYINT NOT NULL, m TINYINT NOT NULL,
            CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name));

        CREATE TABLE IF NOT EXISTS gpkg_extensions (
            table_name TEXT, column_name TEXT, extension_name TEXT NOT NULL,
            definition TEXT NOT NULL, scope TEXT NOT NULL,
            CONSTRAINT ge_tce UNIQUE (table_name, column_name, extension_name));
        """)

        for srid, nombre, org_id, definicion in (
            (-1, "Undefined cartesian SRS", -1, "undefined"),
            (0, "Undefined geographic SRS", 0, "undefined"),
            (SRID_WGS84, "WGS 84", SRID_WGS84,
             'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
             'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]'),
            (SRID_UTM17S, "WGS 84 / UTM zone 17S", SRID_UTM17S,
             'PROJCS["WGS 84 / UTM zone 17S",GEOGCS["WGS 84",DATUM["WGS_1984",'
             'SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],'
             'UNIT["degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],'
             'PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",-81],'
             'PARAMETER["scale_factor",0.9996],PARAMETER["false_easting",500000],'
             'PARAMETER["false_northing",10000000],UNIT["metre",1]]'),
        ):
            c.execute(
                "INSERT OR IGNORE INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
                (nombre, srid, "EPSG" if srid > 0 else "NONE", org_id,
                 definicion, nombre))
        c.commit()

    def close(self) -> None:
        self.con.commit()
        self.con.close()

    def __enter__(self) -> "GeoPackage":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- capas -------------------------------------------------------------
    def crear_capa(self, capa: Capa) -> None:
        """Crea la tabla, la registra en los metadatos y añade el índice espacial."""

        cols = ['"fid" INTEGER PRIMARY KEY AUTOINCREMENT']
        if capa.tiene_geometria:
            cols.append('"geom" BLOB')
        cols += [f.sql() for f in capa.campos]
        self.con.execute(
            f'CREATE TABLE IF NOT EXISTS "{capa.nombre}" ({", ".join(cols)})')

        self.con.execute(
            "INSERT OR REPLACE INTO gpkg_contents "
            "(table_name, data_type, identifier, description, srs_id) "
            "VALUES (?,?,?,?,?)",
            (capa.nombre, "features" if capa.tiene_geometria else "attributes",
             capa.nombre, capa.descripcion, capa.srid))

        if capa.tiene_geometria:
            self.con.execute(
                "INSERT OR REPLACE INTO gpkg_geometry_columns VALUES (?,?,?,?,?,?)",
                (capa.nombre, "geom", capa.tipo_geometria, capa.srid, 0, 0))
            self._indice_espacial(capa.nombre)
        self.con.commit()

    def _indice_espacial(self, tabla: str) -> None:
        """Índice R*Tree. Sin él, dibujar el mapa exige recorrer toda la tabla:
        con 30 000 elementos el móvil tarda segundos por cada desplazamiento."""

        self.con.executescript(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS "rtree_{tabla}_geom"
            USING rtree(id, minx, maxx, miny, maxy);
        """)
        self.con.execute(
            "INSERT OR REPLACE INTO gpkg_extensions VALUES (?,?,?,?,?)",
            (tabla, "geom", "gpkg_rtree_index",
             "http://www.geopackage.org/spec120/#extension_rtree", "write-only"))

    def reindexar(self, tabla: str) -> int:
        """Reconstruye el índice espacial desde las geometrías cargadas."""

        self.con.execute(f'DELETE FROM "rtree_{tabla}_geom"')
        filas = self.con.execute(
            f'SELECT fid, geom FROM "{tabla}" WHERE geom IS NOT NULL').fetchall()
        n = 0
        for f in filas:
            g = leer_geometria(f["geom"])
            if not g:
                continue
            xs = [c[0] for c in g["coords"]]
            ys = [c[1] for c in g["coords"]]
            self.con.execute(
                f'INSERT INTO "rtree_{tabla}_geom" VALUES (?,?,?,?,?)',
                (f["fid"], min(xs), max(xs), min(ys), max(ys)))
            n += 1
        self.con.commit()
        return n

    def actualizar_extension(self, tabla: str) -> None:
        """Escribe la envolvente de la capa en ``gpkg_contents``.

        El móvil la usa para encuadrar el mapa al abrir el paquete sin tener que
        leer una sola geometría."""

        r = self.con.execute(
            f'SELECT MIN(minx), MIN(miny), MAX(maxx), MAX(maxy) '
            f'FROM "rtree_{tabla}_geom"').fetchone()
        if r and r[0] is not None:
            self.con.execute(
                "UPDATE gpkg_contents SET min_x=?, min_y=?, max_x=?, max_y=? "
                "WHERE table_name=?", (r[0], r[1], r[2], r[3], tabla))
            self.con.commit()

    # -- datos -------------------------------------------------------------
    def insertar(self, tabla: str, filas: list[dict]) -> int:
        """Inserta filas. ``geom`` debe venir ya como binario GeoPackage."""

        if not filas:
            return 0
        # Unión de las claves de TODAS las filas, no solo de la primera: las filas
        # de un lote no siempre traen los mismos campos (un cambio de atributo no
        # lleva geometría, uno propagado sí lleva `propagado_de`). Tomando solo
        # las claves de la primera se perderían columnas en silencio, que es la
        # peor forma de perder datos: sin error y sin rastro.
        columnas: list[str] = []
        vistas: set[str] = set()
        for f in filas:
            for k in f:
                if k not in vistas:
                    vistas.add(k)
                    columnas.append(k)
        marcas = ",".join("?" * len(columnas))
        sql = (f'INSERT INTO "{tabla}" ({",".join(chr(34)+c+chr(34) for c in columnas)}) '
               f'VALUES ({marcas})')
        self.con.executemany(sql, [[f.get(c) for c in columnas] for f in filas])
        self.con.commit()
        return len(filas)

    def actualizar(self, tabla: str, valores: dict, *,
                   donde: str, args: tuple = ()) -> int:
        """Actualiza filas y **mantiene el índice espacial al día**.

        El R*Tree no se actualiza solo: es una tabla virtual aparte. Si se
        modifica la geometría sin tocarlo, el elemento sigue existiendo pero deja
        de aparecer en las consultas por ventana —invisible en el mapa aunque el
        dato esté guardado—. El técnico lo vuelve a capturar y queda duplicado.
        """

        if not valores:
            return 0
        sets = ", ".join(f'"{k}" = ?' for k in valores)
        sql = f'UPDATE "{tabla}" SET {sets} WHERE {donde}'
        cur = self.con.execute(sql, [*valores.values(), *args])
        n = cur.rowcount

        if "geom" in valores and n:
            filas = self.con.execute(
                f'SELECT fid, geom FROM "{tabla}" WHERE {donde}', args)
            for f in filas.fetchall():
                env = envolvente(f["geom"])
                if env is None:
                    continue
                self.con.execute(
                    f'INSERT OR REPLACE INTO "rtree_{tabla}_geom" '
                    f"VALUES (?,?,?,?,?)",
                    (f["fid"], env[0], env[2], env[1], env[3]))
        self.con.commit()
        return n

    def contar(self, tabla: str) -> int:
        return int(self.con.execute(f'SELECT COUNT(*) FROM "{tabla}"').fetchone()[0])

    def tablas(self) -> list[str]:
        return [r["table_name"] for r in
                self.con.execute("SELECT table_name FROM gpkg_contents")]

    def leer(self, tabla: str, where: str = "", limite: int | None = None
             ) -> list[dict]:
        sql = f'SELECT * FROM "{tabla}"'
        if where:
            sql += f" WHERE {where}"
        if limite:
            sql += f" LIMIT {int(limite)}"
        return [dict(r) for r in self.con.execute(sql)]

    # -- metadatos del paquete --------------------------------------------
    def escribir_manifiesto(self, datos: dict) -> None:
        """Manifiesto del paquete: qué trae, para quién y desde qué versión.

        Va **dentro** del GeoPackage y no en un archivo aparte porque en campo el
        `.gpkg` viaja solo: si el manifiesto se separa, se pierde la trazabilidad
        de qué versión de la red se editó."""

        self.con.execute(
            "CREATE TABLE IF NOT EXISTS ptnt_manifiesto "
            "(clave TEXT PRIMARY KEY, valor TEXT)")
        for k, v in datos.items():
            self.con.execute(
                "INSERT OR REPLACE INTO ptnt_manifiesto VALUES (?,?)",
                (k, json.dumps(v, ensure_ascii=False, default=str)
                 if not isinstance(v, str) else v))
        self.con.commit()

    def leer_manifiesto(self) -> dict:
        try:
            filas = self.con.execute("SELECT clave, valor FROM ptnt_manifiesto")
        except sqlite3.OperationalError:
            return {}
        out = {}
        for r in filas:
            try:
                out[r["clave"]] = json.loads(r["valor"])
            except (json.JSONDecodeError, TypeError):
                out[r["clave"]] = r["valor"]
        return out


def envolvente(blob: bytes | None) -> tuple[float, float, float, float] | None:
    """Caja envolvente de una geometría GeoPackage: ``(minx, miny, maxx, maxy)``.

    Se calcula desde las coordenadas y no se lee de la cabecera: un productor
    externo puede escribir la geometría sin envolvente, y en ese caso la
    cabecera no la trae.
    """

    g = leer_geometria(blob) if blob else None
    if not g or not g.get("coords"):
        return None
    xs = [c[0] for c in g["coords"]]
    ys = [c[1] for c in g["coords"]]
    return (min(xs), min(ys), max(xs), max(ys))


def ahora_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
