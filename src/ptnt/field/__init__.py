"""Trabajo de campo: paquetes GeoPackage, órdenes, edición móvil y sincronización.

El ciclo completo: el análisis dice dónde ir -> se asignan órdenes a un técnico ->
se arma un GeoPackage con la red del área y la cartografía offline -> el técnico
edita en campo sin señal -> sube los cambios -> el supervisor los revisa -> los
aceptados actualizan el modelo y disparan el recálculo del balance y del ranking.
"""

from ptnt.field.distribute import Reparto, asignar_reparto, repartir_ordenes
from ptnt.field.workdef import (
    DefinicionTrabajo,
    TipoTrabajo,
    desde_plan,
    por_alimentador,
    por_area,
    por_lista,
    por_sector,
    unir,
)
from ptnt.field.gpkg import Campo, Capa, GeoPackage, linea, punto
from ptnt.field.simulator import ResumenJornada, SimuladorCampo
from ptnt.field.package import (
    AreaTrabajo,
    ResultadoPaquete,
    construir_paquete,
    construir_paquetes,
    huella_paquete,
    resumen_paquetes,
)
from ptnt.field.store import AlmacenCampo, ConflictoConcurrencia
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
    "TransicionInvalida", "AlmacenCampo", "ConflictoConcurrencia",
    "Reparto", "repartir_ordenes", "asignar_reparto",
    "TipoTrabajo", "DefinicionTrabajo", "por_alimentador", "por_sector",
    "por_area", "por_lista", "desde_plan", "unir",
    "construir_paquete", "construir_paquetes", "resumen_paquetes",
    "ResultadoPaquete", "AreaTrabajo", "huella_paquete",
    "SimuladorCampo", "ResumenJornada",
    "recibir_paquete", "revisar", "aplicar", "LoteSincronizacion",
    "EstadoRevision", "Severidad", "HistoricoCambios",
]
