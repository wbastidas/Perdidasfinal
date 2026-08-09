"""Trabajo de campo: paquetes GeoPackage, órdenes, edición móvil y sincronización.

El ciclo completo: el análisis dice dónde ir -> se asignan órdenes a un técnico ->
se arma un GeoPackage con la red del área y la cartografía offline -> el técnico
edita en campo sin señal -> sube los cambios -> el supervisor los revisa -> los
aceptados actualizan el modelo y disparan el recálculo del balance y del ranking.
"""

from ptnt.field.gpkg import Campo, Capa, GeoPackage, linea, punto
from ptnt.field.package import (
    AreaTrabajo,
    ResultadoPaquete,
    construir_paquete,
    huella_paquete,
)
from ptnt.field.schema import (
    VERSION_ESQUEMA,
    capas_red,
    capas_trabajo,
    esquema_para_movil,
    todas_las_capas,
)
from ptnt.field.sync import (
    EstadoRevision,
    HistoricoCambios,
    LoteSincronizacion,
    Severidad,
    aplicar,
    recibir_paquete,
    revisar,
)
from ptnt.field.topology_edit import (
    Conexion,
    Elemento,
    GrafoEdicion,
    TipoRelacion,
    construir_grafo,
)
from ptnt.field.workorders import (
    Asignacion,
    EstadoOrden,
    RegistroCampo,
    RolCampo,
    TransicionInvalida,
    UsuarioCampo,
)

__all__ = [
    "GeoPackage", "Capa", "Campo", "punto", "linea",
    "VERSION_ESQUEMA", "capas_red", "capas_trabajo", "todas_las_capas",
    "esquema_para_movil",
    "GrafoEdicion", "Elemento", "Conexion", "TipoRelacion", "construir_grafo",
    "RegistroCampo", "UsuarioCampo", "Asignacion", "RolCampo", "EstadoOrden",
    "TransicionInvalida",
    "construir_paquete", "ResultadoPaquete", "AreaTrabajo", "huella_paquete",
    "recibir_paquete", "revisar", "aplicar", "LoteSincronizacion",
    "EstadoRevision", "Severidad", "HistoricoCambios",
]
