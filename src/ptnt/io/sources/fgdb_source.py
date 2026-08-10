"""Conector para File Geodatabase de ArcGIS (Esri FileGDB).

La red se entrega como una **File Geodatabase** creada con ArcGIS Desktop, con la
red geométrica ``Electrico_RedGeom`` y geometrías ``ST_Geometry``. Se lee con el
driver **OpenFileGDB** de GDAL vía ``pyogrio`` (o ``fiona``), que no requiere
licencia de ArcGIS. La geometría se expone como WKB + centroide (x, y); la
conectividad eléctrica se resuelve con ``CIRCUITSOURCEGUID`` /
``PARENTCIRCUITSOURCEGUID`` y por topología (ver ``ptnt.topology``), no por la red
geométrica de ArcGIS (descontinuada en ArcGIS Pro).

Extra opcional: ``pip install pyogrio`` (o ``geopandas``). Si no está, el conector
da un error claro.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ptnt.io.sources.base import SourceConnector, SourceError

# CRS esperado: UTM 17S WGS84
_EXPECTED_EPSG = 32717


class FgdbSource(SourceConnector):
    """Lee feature classes y tablas de una File Geodatabase (.gdb)."""

    def __init__(self, fuente):
        super().__init__(fuente)
        self._backend = None
        try:
            import pyogrio  # noqa: F401

            self._backend = "pyogrio"
        except Exception:
            try:
                import fiona  # noqa: F401

                self._backend = "fiona"
            except Exception as exc:  # pragma: no cover - depende de instalación
                raise SourceError(
                    "Lectura de FGDB requiere 'pyogrio' o 'fiona' (GDAL con driver "
                    "OpenFileGDB). Instale: pip install pyogrio"
                ) from exc

    @property
    def ruta(self) -> Path:
        if not self.fuente.ruta:
            raise SourceError(f"fuente '{self.nombre}' sin 'ruta' a la .gdb")
        return Path(self.fuente.ruta)

    def test_connection(self) -> bool:
        if not self.ruta.exists():
            raise SourceError(f"File Geodatabase no encontrada: '{self.ruta}'")
        # listar capas verifica que el driver puede abrirla
        self.list_layers()
        return True

    def list_layers(self) -> list[str]:
        """Lista las clases (feature classes/tablas) de la geodatabase."""

        try:
            if self._backend == "pyogrio":
                import pyogrio

                info = pyogrio.list_layers(str(self.ruta))
                return [row[0] for row in info]
            import fiona

            return list(fiona.listlayers(str(self.ruta)))
        except Exception as exc:
            raise SourceError(f"No se pudieron listar capas de '{self.ruta}': {exc}") from exc

    def read_query(self, query: str, params: dict | None = None) -> pd.DataFrame:
        # OpenFileGDB soporta SQL básico vía GDAL; se expone la capa por nombre
        raise SourceError(
            "El conector FGDB lee por capa (read_table), no SQL arbitrario. "
            "Use read_table('<NombreClase>')."
        )

    def read_table(
        self, tabla: str, columnas: list[str] | None = None, limite: int | None = None
    ) -> pd.DataFrame:
        """Lee una feature class/tabla y devuelve un DataFrame.

        Las geometrías se convierten a WKB en la columna ``geom_wkb`` y su
        centroide a ``x``/``y``. Verifica el CRS esperado (EPSG:32717) y avisa si
        difiere en vez de reproyectar en silencio.
        """

        try:
            if self._backend == "pyogrio":
                df = self._read_pyogrio(tabla, columnas, limite)
            else:
                df = self._read_fiona(tabla, columnas, limite)
        except SourceError:
            raise
        except Exception as exc:
            raise SourceError(f"Error leyendo capa '{tabla}' de '{self.ruta}': {exc}") from exc
        return df

    def _read_pyogrio(self, tabla, columnas, limite) -> pd.DataFrame:
        import pyogrio

        kw = {}
        if columnas:
            kw["columns"] = columnas
        if limite:
            kw["max_features"] = limite
        # read_dataframe requiere geopandas; read (raw) evita esa dependencia
        try:
            import geopandas  # noqa: F401

            gdf = pyogrio.read_dataframe(str(self.ruta), layer=tabla, **kw)
            return self._normalize_geodf(gdf)
        except Exception:
            meta, geometry, field_data = _pyogrio_raw(pyogrio, str(self.ruta), tabla, kw)
            df = pd.DataFrame({name: col for name, col in zip(meta["fields"], field_data)})
            if geometry is not None:
                df["geom_wkb"] = list(geometry)
            return df

    def _read_fiona(self, tabla, columnas, limite) -> pd.DataFrame:
        import fiona
        from shapely.geometry import shape

        registros = []
        with fiona.open(str(self.ruta), layer=tabla) as src:
            crs_epsg = (src.crs or {}).get("init", "").replace("epsg:", "")
            for i, feat in enumerate(src):
                if limite and i >= limite:
                    break
                props = dict(feat["properties"])
                geom = feat.get("geometry")
                if geom is not None:
                    g = shape(geom)
                    props["geom_wkb"] = g.wkb
                    c = g.centroid
                    props["x"], props["y"] = c.x, c.y
                registros.append(props)
        df = pd.DataFrame(registros)
        if columnas:
            df = df[[c for c in columnas if c in df.columns]]
        return df

    def _normalize_geodf(self, gdf) -> pd.DataFrame:
        # verificar CRS
        try:
            epsg = gdf.crs.to_epsg() if gdf.crs else None
            if epsg and epsg != _EXPECTED_EPSG:
                raise SourceError(
                    f"CRS inesperado EPSG:{epsg} (se esperaba {_EXPECTED_EPSG}). "
                    "Abortando en lugar de reproyectar en silencio."
                )
        except SourceError:
            raise
        except Exception:
            pass
        df = pd.DataFrame(gdf.drop(columns=gdf.geometry.name, errors="ignore"))
        geom = gdf.geometry
        df["geom_wkb"] = geom.apply(lambda g: g.wkb if g is not None else None)
        df["x"] = geom.centroid.x
        df["y"] = geom.centroid.y
        return df


def _pyogrio_raw(pyogrio, path, layer, kw):  # pragma: no cover - ruta sin geopandas
    """Lectura cruda con pyogrio.raw cuando geopandas no está disponible."""
    from pyogrio.raw import read

    return read(path, layer=layer, **kw)
