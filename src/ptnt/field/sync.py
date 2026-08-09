"""Sincronización: subir los cambios de campo, revisarlos y recalcular.

Es la etapa donde el trabajo de campo se convierte —o no— en una actualización
del modelo. El principio que la gobierna: **nada entra al modelo sin pasar por
revisión**. Un técnico puede equivocarse de elemento, arrastrar sin querer, o
capturar con el GPS derivando bajo un árbol; aceptar sus ediciones a ciegas
degradaría el SIG en vez de mejorarlo.

El ciclo completo::

    móvil ──► paquete de retorno (.gpkg con el diario de cambios)
                    │
                    ▼
            [1] Validación técnica     ¿el paquete es el que se entregó?
                    │                  ¿los cambios son coherentes?
                    ▼
            [2] Revisión por lote      el supervisor ve TODO lo que cambió,
                    │                  con antes/después y foto de evidencia
                    ▼
            [3] Aceptación parcial     se aceptan unos cambios y se rechazan otros
                    │
                    ▼
            [4] Aplicación al modelo   se escribe en el modelo canónico
                    │
                    ▼
            [5] Invalidación selectiva  qué etapas hay que recalcular
                    │
                    ▼
            [6] Recálculo y re-ranking  balance y focalización actualizados

El paso 5 reutiliza el versionado de topología que ya existe
(`ptnt.topology.versioning`): un cambio de atributo no obliga a rehacer la
topología, y una maniobra de seccionador no obliga a rehacer las pérdidas. Sin
esa selectividad, cada visita de campo dispararía un recálculo completo de la
unidad de negocio.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import pandas as pd

from ptnt.field.gpkg import GeoPackage, leer_geometria


class EstadoRevision(str, Enum):
    PENDIENTE = "PENDIENTE"
    ACEPTADO = "ACEPTADO"
    RECHAZADO = "RECHAZADO"


class Severidad(str, Enum):
    OK = "OK"
    # Informativo no es una advertencia: describe algo esperado que conviene
    # decir. Mezclarlos haría que el supervisor dejara de leer las advertencias.
    INFORMATIVO = "INFORMATIVO"
    ADVERTENCIA = "ADVERTENCIA"
    BLOQUEANTE = "BLOQUEANTE"


@dataclass
class Hallazgo:
    """Un problema detectado al validar el paquete de retorno."""

    severidad: Severidad
    codigo: str
    detalle: str
    elemento_guid: str = ""


@dataclass
class CambioRecibido:
    """Un cambio del diario, tal como llegó del móvil."""

    guid: str
    secuencia: int
    capa: str
    elemento_guid: str
    operacion: str
    campo: str = ""
    valor_antes: str | None = None
    valor_despues: str | None = None
    geom_antes: str | None = None
    geom_despues: str | None = None
    orden_trabajo: str = ""
    autor: str = ""
    ocurrido_en: str = ""
    lat_dispositivo: float | None = None
    lon_dispositivo: float | None = None
    precision_m: float | None = None
    motivo: str = ""
    propagado_de: str = ""
    estado_revision: EstadoRevision = EstadoRevision.PENDIENTE

    @property
    def afecta_geometria(self) -> bool:
        return self.operacion in ("MOVER", "CREAR", "ELIMINAR") or bool(
            self.geom_despues)

    @property
    def afecta_topologia(self) -> bool:
        """Qué cambia la conectividad: crear, eliminar y **reconectar**.

        Mover no: arrastrar un poste treinta metros no cambia de qué cuelga.
        Reconectar sí, aunque no toque un solo píxel del mapa — y es el caso que
        más se paga en el balance.
        """

        return self.operacion in ("CREAR", "ELIMINAR", "RECONECTAR")

    @property
    def afecta_dos_zonas(self) -> bool:
        """Una reconexión mueve energía de una zona a otra.

        Es el único cambio que altera **dos** balances con una sola edición: la
        zona que pierde el cliente y la que lo gana. Si solo se recalcula la del
        elemento, una de las dos queda con PNT inventada.
        """

        return self.operacion == "RECONECTAR"

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["estado_revision"] = self.estado_revision.value
        return d


@dataclass
class LoteSincronizacion:
    """Todo lo que llegó de una sincronización, listo para revisar."""

    lote_id: str
    usuario: str
    paquete_id: str
    recibido_en: str
    cambios: list[CambioRecibido] = field(default_factory=list)
    fotos: list[dict] = field(default_factory=list)
    ordenes: list[dict] = field(default_factory=list)
    hallazgos: list[Hallazgo] = field(default_factory=list)
    version_red_origen: str = ""
    # Cambios que llegaron sin autor y se completaron con el dueño del paquete.
    autores_completados: int = 0
    # Cambios de jornadas anteriores que ya se habían enviado y se ignoraron.
    cambios_ya_sincronizados: int = 0

    @property
    def bloqueado(self) -> bool:
        return any(h.severidad is Severidad.BLOQUEANTE for h in self.hallazgos)

    def resumen(self) -> dict:
        por_op: dict[str, int] = {}
        por_capa: dict[str, int] = {}
        for c in self.cambios:
            por_op[c.operacion] = por_op.get(c.operacion, 0) + 1
            por_capa[c.capa] = por_capa.get(c.capa, 0) + 1
        return {
            "lote_id": self.lote_id, "usuario": self.usuario,
            "recibido_en": self.recibido_en,
            "cambios": len(self.cambios),
            "propagados": sum(1 for c in self.cambios if c.propagado_de),
            "por_operacion": por_op, "por_capa": por_capa,
            "fotos": len(self.fotos), "ordenes": len(self.ordenes),
            "ya_sincronizados": self.cambios_ya_sincronizados,
            "hallazgos": len(self.hallazgos),
            "bloqueado": self.bloqueado,
        }

    def to_dataframe(self) -> pd.DataFrame:
        """Tabla de revisión: lo que el supervisor ve y decide."""

        if not self.cambios:
            return pd.DataFrame()
        return pd.DataFrame([{
            "secuencia": c.secuencia, "capa": c.capa,
            "elemento": c.elemento_guid, "operacion": c.operacion,
            "campo": c.campo, "antes": c.valor_antes, "despues": c.valor_despues,
            "geometria": "sí" if c.geom_despues else "",
            "propagado": "sí" if c.propagado_de else "",
            "orden": c.orden_trabajo, "autor": c.autor,
            "cuando": c.ocurrido_en, "motivo": c.motivo,
            "estado": c.estado_revision.value,
        } for c in self.cambios])


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _verdadero(v) -> bool:
    """SQLite guarda los booleanos como 0/1, pero un cliente puede mandar texto."""

    return str(v).strip().lower() in ("1", "true", "t", "sí", "si", "yes")


# --------------------------------------------------------------------------- #
# 1–2. Recepción y validación
# --------------------------------------------------------------------------- #
def recibir_paquete(
    ruta_gpkg: str | Path,
    *,
    usuario_esperado: str | None = None,
    version_red_actual: str = "",
) -> LoteSincronizacion:
    """Lee el paquete de retorno y lo valida antes de dejar revisar nada."""

    ruta = Path(ruta_gpkg)
    lote = LoteSincronizacion(
        lote_id=str(uuid.uuid4()), usuario="", paquete_id="",
        recibido_en=_ahora())

    if not ruta.exists():
        lote.hallazgos.append(Hallazgo(
            Severidad.BLOQUEANTE, "SYNC01", f"No existe el archivo: {ruta}"))
        return lote

    with GeoPackage(ruta) as gp:
        man = gp.leer_manifiesto()
        lote.usuario = str(man.get("usuario", ""))
        lote.paquete_id = str(man.get("paquete_id", ""))
        lote.version_red_origen = str(man.get("version_red", ""))

        if usuario_esperado and lote.usuario != usuario_esperado:
            lote.hallazgos.append(Hallazgo(
                Severidad.BLOQUEANTE, "SYNC02",
                f"El paquete fue emitido para '{lote.usuario}' pero lo sube "
                f"'{usuario_esperado}'. No se acepta: rompería la trazabilidad "
                "de quién editó qué."))

        # Un paquete editado contra una versión vieja de la red puede pisar
        # cambios posteriores. No se bloquea —el trabajo de campo es válido—
        # pero se advierte para que la revisión sea más cuidadosa.
        if (version_red_actual and lote.version_red_origen
                and lote.version_red_origen != version_red_actual):
            lote.hallazgos.append(Hallazgo(
                Severidad.ADVERTENCIA, "SYNC03",
                f"El paquete se generó sobre la versión de red "
                f"'{lote.version_red_origen}' y la actual es "
                f"'{version_red_actual}'. Revise si algún cambio pisa una "
                "actualización posterior del SIG."))

        try:
            filas = gp.leer("ptnt_cambio")
        except Exception:
            filas = []
        # Un cambio sin autor es un defecto del cliente móvil. Se completa con el
        # dueño del paquete —el trabajo de campo no se descarta por un bug de la
        # app— pero se **cuenta y se reporta**: taparlo en silencio convertiría la
        # validación en código muerto y el defecto seguiría llegando cada mes.
        # Un trabajo de varios días sube avance cada tarde y el diario acumula.
        # Lo ya enviado se ignora: procesarlo otra vez duplicaría el cambio en el
        # histórico y el supervisor tendría que revisar dos veces lo mismo.
        ya_enviados = [f for f in filas if _verdadero(f.get("sincronizado"))]
        filas = [f for f in filas if not _verdadero(f.get("sincronizado"))]
        lote.cambios_ya_sincronizados = len(ya_enviados)

        sin_autor_original = sum(1 for f in filas if not str(f.get("autor") or ""))
        lote.autores_completados = sin_autor_original

        for f in filas:
            lote.cambios.append(CambioRecibido(
                guid=str(f.get("guid") or uuid.uuid4()),
                secuencia=int(f.get("secuencia") or 0),
                capa=str(f.get("capa", "")),
                elemento_guid=str(f.get("elemento_guid", "")),
                operacion=str(f.get("operacion", "")),
                campo=str(f.get("campo") or ""),
                valor_antes=f.get("valor_antes"),
                valor_despues=f.get("valor_despues"),
                geom_antes=f.get("geom_antes"),
                geom_despues=f.get("geom_despues"),
                orden_trabajo=str(f.get("orden_trabajo") or ""),
                autor=str(f.get("autor") or lote.usuario),
                ocurrido_en=str(f.get("ocurrido_en") or ""),
                lat_dispositivo=f.get("lat_dispositivo"),
                lon_dispositivo=f.get("lon_dispositivo"),
                precision_m=f.get("precision_m"),
                motivo=str(f.get("motivo") or ""),
                propagado_de=str(f.get("propagado_de") or ""),
            ))
        lote.cambios.sort(key=lambda c: c.secuencia)

        try:
            lote.fotos = gp.leer("ptnt_foto")
        except Exception:
            lote.fotos = []
        try:
            lote.ordenes = gp.leer("ptnt_orden_trabajo")
        except Exception:
            lote.ordenes = []

    lote.hallazgos.extend(_validar(lote))
    return lote


def _validar(lote: LoteSincronizacion) -> list[Hallazgo]:
    """Controles de coherencia sobre lo recibido."""

    h: list[Hallazgo] = []

    if not lote.cambios and not lote.fotos:
        # Si trae cambios de días anteriores ya enviados, no es una visita vacía:
        # es una sincronización sin novedades, que en un trabajo largo es normal.
        if lote.cambios_ya_sincronizados:
            h.append(Hallazgo(
                Severidad.INFORMATIVO, "SYNC19",
                f"Sin novedades nuevas. El paquete trae "
                f"{lote.cambios_ya_sincronizados} cambio(s) ya enviados en "
                "jornadas anteriores; no se procesan otra vez."))
        else:
            h.append(Hallazgo(
                Severidad.ADVERTENCIA, "SYNC10",
                "El paquete no trae cambios ni fotos: la visita no dejó "
                "evidencia."))

    # Secuencia sin huecos: el diario es un log incremental. Un hueco significa
    # que se perdieron ediciones, y aceptar el resto dejaría el modelo a medias.
    secuencias = [c.secuencia for c in lote.cambios]
    if secuencias:
        esperadas = set(range(min(secuencias), max(secuencias) + 1))
        faltan = sorted(esperadas - set(secuencias))
        if faltan:
            h.append(Hallazgo(
                Severidad.BLOQUEANTE, "SYNC11",
                f"El diario de cambios tiene {len(faltan)} hueco(s) de secuencia "
                f"(desde {faltan[0]}). Se perdieron ediciones: no se puede "
                "aceptar el lote a medias."))

    # Sin autor Y sin dueño de paquete: no hay forma de atribuir la edición.
    sin_autor = [c for c in lote.cambios if not c.autor]
    if sin_autor:
        h.append(Hallazgo(
            Severidad.BLOQUEANTE, "SYNC12",
            f"{len(sin_autor)} cambio(s) sin autor y sin usuario en el "
            "manifiesto: la edición de red debe ser siempre atribuible."))
    elif lote.autores_completados:
        h.append(Hallazgo(
            Severidad.ADVERTENCIA, "SYNC16",
            f"{lote.autores_completados} cambio(s) llegaron sin autor y se "
            f"atribuyeron a '{lote.usuario}' por el manifiesto. Es un defecto de "
            "la aplicación móvil: repórtelo, porque volverá a ocurrir."))

    # Precisión del GPS al editar: una captura con 50 m de error no sirve para
    # corregir la posición de un medidor.
    imprecisos = [c for c in lote.cambios
                  if c.operacion in ("CREAR", "MOVER")
                  and c.precision_m is not None and c.precision_m > 25]
    if imprecisos:
        h.append(Hallazgo(
            Severidad.ADVERTENCIA, "SYNC13",
            f"{len(imprecisos)} cambio(s) de geometría capturados con precisión "
            "GPS peor a 25 m. Verifique antes de aceptar la nueva ubicación."))

    # Fotos sin metadatos: sin ubicación ni hora, una foto no es evidencia.
    sin_meta = [f for f in lote.fotos
                if not f.get("tomada_en") or f.get("lat") is None]
    if sin_meta:
        h.append(Hallazgo(
            Severidad.ADVERTENCIA, "SYNC14",
            f"{len(sin_meta)} foto(s) sin ubicación o fecha de captura: no "
            "sirven como evidencia del hallazgo."))

    # Una reconexión sin origen y destino no se puede aplicar: no se sabe de qué
    # zona sale el consumo ni a cuál entra, que es justamente el dato que importa.
    incompletas = [c for c in lote.cambios
                   if c.operacion == "RECONECTAR"
                   and not (c.valor_antes and c.valor_despues)]
    if incompletas:
        h.append(Hallazgo(
            Severidad.BLOQUEANTE, "SYNC17",
            f"{len(incompletas)} reconexión(es) sin transformador de origen o de "
            "destino. Sin ambos no se puede mover el consumo de una zona a otra."))

    # Reconectar a sí mismo es un toque accidental, no una corrección.
    nulas = [c for c in lote.cambios
             if c.operacion == "RECONECTAR" and c.valor_antes == c.valor_despues]
    if nulas:
        h.append(Hallazgo(
            Severidad.ADVERTENCIA, "SYNC18",
            f"{len(nulas)} reconexión(es) al mismo transformador que ya tenía: "
            "probablemente un toque accidental. Revíselas antes de aceptar."))

    huerfanos = [c for c in lote.cambios if not c.elemento_guid]
    if huerfanos:
        h.append(Hallazgo(
            Severidad.BLOQUEANTE, "SYNC15",
            f"{len(huerfanos)} cambio(s) sin elemento asociado."))

    return h


# --------------------------------------------------------------------------- #
# 3. Revisión y aceptación
# --------------------------------------------------------------------------- #
def revisar(
    lote: LoteSincronizacion,
    *,
    aceptar: list[int] | None = None,
    rechazar: list[int] | None = None,
    aceptar_todo: bool = False,
    revisor: str = "",
    motivo_rechazo: str = "",
) -> dict:
    """Marca cambios como aceptados o rechazados, por número de secuencia.

    La aceptación es **parcial por diseño**: en una misma visita el técnico puede
    haber corregido bien tres medidores y haber movido mal un poste. Obligar a
    aceptar o rechazar todo el lote llevaría a aceptar errores por no perder el
    trabajo bueno.
    """

    if lote.bloqueado:
        raise ValueError(
            "El lote tiene hallazgos bloqueantes: corríjalos antes de revisar. "
            + "; ".join(h.detalle for h in lote.hallazgos
                        if h.severidad is Severidad.BLOQUEANTE))

    aceptar = set(aceptar or [])
    rechazar = set(rechazar or [])
    n_ok = n_no = 0
    for c in lote.cambios:
        if aceptar_todo or c.secuencia in aceptar:
            c.estado_revision = EstadoRevision.ACEPTADO
            n_ok += 1
        elif c.secuencia in rechazar:
            c.estado_revision = EstadoRevision.RECHAZADO
            c.motivo = (c.motivo + " | " if c.motivo else "") + (
                f"RECHAZADO por {revisor or 'revisión'}: {motivo_rechazo}"
                if motivo_rechazo else f"RECHAZADO por {revisor or 'revisión'}")
            n_no += 1

    # Coherencia de la propagación: aceptar el movimiento de un cliente y
    # rechazar el arrastre de su acometida dejaría la red desconectada.
    inconsistentes = []
    aceptados = {c.elemento_guid for c in lote.cambios
                 if c.estado_revision is EstadoRevision.ACEPTADO}
    for c in lote.cambios:
        if (c.propagado_de and c.estado_revision is EstadoRevision.RECHAZADO
                and c.propagado_de in aceptados):
            inconsistentes.append(c.secuencia)

    return {
        "aceptados": n_ok, "rechazados": n_no,
        "pendientes": sum(1 for c in lote.cambios
                          if c.estado_revision is EstadoRevision.PENDIENTE),
        "propagaciones_inconsistentes": inconsistentes,
        "advertencia": (
            f"Se rechazaron {len(inconsistentes)} cambio(s) propagados cuyo "
            "movimiento de origen SÍ fue aceptado: la red quedaría desconectada "
            "en esos puntos. Acéptelos o rechace también el movimiento de origen."
        ) if inconsistentes else "",
    }


# --------------------------------------------------------------------------- #
# 4–5. Aplicación al modelo e invalidación
# --------------------------------------------------------------------------- #
ETAPAS_POR_CAMBIO = {
    # Qué hay que recalcular según lo que se tocó. Reutiliza el criterio del
    # versionado de topología: un cambio de atributo no rehace la conectividad.
    "topologia": {"CREAR", "ELIMINAR", "RECONECTAR"},
    "atributos": {"MODIFICAR"},
    "geometria": {"MOVER"},
    # La reconexión se lista aparte porque además obliga a recalcular la zona de
    # ORIGEN, no solo la de destino.
    "conectividad": {"RECONECTAR"},
}


@dataclass
class ResultadoAplicacion:
    aplicados: int = 0
    rechazados: int = 0
    capas_afectadas: set[str] = field(default_factory=set)
    elementos_afectados: set[str] = field(default_factory=set)
    etapas_a_recalcular: list[str] = field(default_factory=list)
    alimentadores_afectados: set[str] = field(default_factory=set)
    reconexiones: int = 0
    detalle: str = ""


def aplicar(lote: LoteSincronizacion, *,
            feeder_por_elemento: dict[str, str] | None = None
            ) -> ResultadoAplicacion:
    """Determina qué se aplica y **qué etapas hay que recalcular**.

    No escribe el modelo canónico —eso lo hace la capa de persistencia— sino que
    produce el plan: qué elementos cambian, de qué alimentadores, y qué etapas
    del análisis quedan invalidadas. Separar la decisión de la escritura permite
    revisar el impacto antes de tocar nada.
    """

    res = ResultadoAplicacion()
    feeder_por_elemento = feeder_por_elemento or {}
    ops: set[str] = set()

    for c in lote.cambios:
        if c.estado_revision is EstadoRevision.ACEPTADO:
            res.aplicados += 1
            res.capas_afectadas.add(c.capa)
            res.elementos_afectados.add(c.elemento_guid)
            ops.add(c.operacion)
            f = feeder_por_elemento.get(c.elemento_guid)
            if f:
                res.alimentadores_afectados.add(f)
            if c.afecta_dos_zonas:
                # El transformador de origen y el de destino: el balance de los
                # dos cambia. Quedarse solo con el del cliente dejaría la zona
                # que lo perdió con energía facturada que ya no le corresponde.
                for guid in (c.valor_antes, c.valor_despues):
                    res.elementos_afectados.add(str(guid or ""))
                    fz = feeder_por_elemento.get(str(guid or ""))
                    if fz:
                        res.alimentadores_afectados.add(fz)
                res.elementos_afectados.discard("")
                res.reconexiones += 1
        elif c.estado_revision is EstadoRevision.RECHAZADO:
            res.rechazados += 1

    etapas: set[str] = set()
    if ops & ETAPAS_POR_CAMBIO["conectividad"]:
        # Reconectar mueve consumo de una zona a otra: hay que rehacer el
        # balance de las dos y volver a preguntar dónde inspeccionar, porque el
        # ranking de ambas cambia.
        etapas |= {"topologia", "flujo", "perdidas", "balance", "focalizacion",
                   "ranking"}
    if ops & ETAPAS_POR_CAMBIO["topologia"]:
        etapas |= {"topologia", "flujo", "perdidas", "balance", "focalizacion"}
    if ops & ETAPAS_POR_CAMBIO["geometria"]:
        # Mover no cambia la conectividad, pero sí las longitudes de tramo y por
        # tanto las pérdidas; y cambia los sectores geográficos de campo.
        etapas |= {"perdidas", "flujo", "balance", "focalizacion"}
    if ops & ETAPAS_POR_CAMBIO["atributos"]:
        etapas |= {"perdidas", "flujo", "balance"}
    # Un cambio en un cliente (hallazgo, medidor) altera el ranking de sospecha.
    if "ptnt_cliente" in res.capas_afectadas:
        etapas |= {"ranking", "focalizacion"}

    res.etapas_a_recalcular = sorted(etapas)
    res.detalle = (
        f"{res.aplicados} cambio(s) aceptado(s) sobre "
        f"{len(res.elementos_afectados)} elemento(s) de "
        f"{len(res.capas_afectadas)} capa(s). "
        + (f"{res.reconexiones} reconexión(es) de consumidor: cambian el "
           f"balance de la zona que pierde el cliente y de la que lo gana. "
           if res.reconexiones else "")
        + (f"Alimentadores afectados: "
           f"{', '.join(sorted(res.alimentadores_afectados))}. "
           if res.alimentadores_afectados else "")
        + (f"Recalcular: {', '.join(res.etapas_a_recalcular)}."
           if res.etapas_a_recalcular else "Sin recálculo necesario.")
    )
    return res


# --------------------------------------------------------------------------- #
# Histórico de sincronizaciones
# --------------------------------------------------------------------------- #
class HistoricoCambios:
    """Registro permanente de toda modificación de la red, venga de donde venga.

    Acumula tanto las ediciones de campo como las cargas desde archivo (FGDB,
    SQL), porque la pregunta de auditoría es siempre la misma: *¿quién cambió
    este elemento, cuándo y por qué?* — y la respuesta no puede depender de por
    qué puerta entró el cambio.
    """

    COLUMNAS = [
        "cambio_id", "lote_id", "origen", "capa", "elemento_guid", "operacion",
        "campo", "valor_antes", "valor_despues", "geom_antes", "geom_despues",
        "orden_trabajo", "autor", "ocurrido_en", "registrado_en",
        "lat_dispositivo", "lon_dispositivo", "precision_m", "motivo",
        "propagado_de", "estado_revision", "alimentador",
    ]

    def __init__(self, ruta: str | Path):
        self.ruta = Path(ruta)
        self.df = self._cargar()

    def _cargar(self) -> pd.DataFrame:
        if not self.ruta.exists():
            return pd.DataFrame(columns=self.COLUMNAS)
        try:
            df = (pd.read_parquet(self.ruta) if self.ruta.suffix == ".parquet"
                  else pd.read_csv(self.ruta))
        except Exception:
            return pd.DataFrame(columns=self.COLUMNAS)
        for c in self.COLUMNAS:
            if c not in df.columns:
                df[c] = None
        return df[self.COLUMNAS]

    def registrar_lote(self, lote: LoteSincronizacion, *,
                       feeder_por_elemento: dict[str, str] | None = None) -> int:
        feeder_por_elemento = feeder_por_elemento or {}
        ahora = _ahora()
        filas = [{
            "cambio_id": c.guid, "lote_id": lote.lote_id, "origen": "MOVIL",
            "capa": c.capa, "elemento_guid": c.elemento_guid,
            "operacion": c.operacion, "campo": c.campo,
            "valor_antes": c.valor_antes, "valor_despues": c.valor_despues,
            "geom_antes": c.geom_antes, "geom_despues": c.geom_despues,
            "orden_trabajo": c.orden_trabajo, "autor": c.autor,
            "ocurrido_en": c.ocurrido_en, "registrado_en": ahora,
            "lat_dispositivo": c.lat_dispositivo,
            "lon_dispositivo": c.lon_dispositivo,
            "precision_m": c.precision_m, "motivo": c.motivo,
            "propagado_de": c.propagado_de,
            "estado_revision": c.estado_revision.value,
            "alimentador": feeder_por_elemento.get(c.elemento_guid, ""),
        } for c in lote.cambios]
        if not filas:
            return 0
        self.df = pd.concat([self.df, pd.DataFrame(filas)[self.COLUMNAS]],
                            ignore_index=True)
        return len(filas)

    def registrar_carga_archivo(
        self, *, origen: str, capa: str, elementos: list[str],
        operacion: str = "MODIFICAR", autor: str = "carga_masiva",
        motivo: str = "",
    ) -> int:
        """Registra una carga desde archivo con el mismo formato que las de campo."""

        ahora = _ahora()
        lote = str(uuid.uuid4())
        filas = [{
            "cambio_id": str(uuid.uuid4()), "lote_id": lote,
            "origen": f"ARCHIVO:{origen}", "capa": capa,
            "elemento_guid": g, "operacion": operacion, "campo": "",
            "valor_antes": None, "valor_despues": None,
            "geom_antes": None, "geom_despues": None, "orden_trabajo": "",
            "autor": autor, "ocurrido_en": ahora, "registrado_en": ahora,
            "lat_dispositivo": None, "lon_dispositivo": None,
            "precision_m": None, "motivo": motivo, "propagado_de": "",
            "estado_revision": EstadoRevision.ACEPTADO.value, "alimentador": "",
        } for g in elementos]
        if not filas:
            return 0
        self.df = pd.concat([self.df, pd.DataFrame(filas)[self.COLUMNAS]],
                            ignore_index=True)
        return len(filas)

    def save(self) -> Path:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        try:
            if self.ruta.suffix == ".parquet":
                self.df.to_parquet(self.ruta, index=False)
            else:
                self.df.to_csv(self.ruta, index=False)
        except Exception:
            self.ruta = self.ruta.with_suffix(".csv")
            self.df.to_csv(self.ruta, index=False)
        return self.ruta

    # -- consulta ----------------------------------------------------------
    def historia_de(self, elemento_guid: str) -> pd.DataFrame:
        """Toda la historia de un elemento, para responder "¿por qué está así?"."""

        d = self.df[self.df["elemento_guid"] == elemento_guid]
        return d.sort_values("ocurrido_en")

    def resumen_por_origen(self) -> pd.DataFrame:
        if self.df.empty:
            return pd.DataFrame()
        d = self.df.copy()
        d["fuente"] = d["origen"].astype(str).str.split(":").str[0]
        return (d.groupby(["fuente", "operacion"])
                .agg(cambios=("cambio_id", "count"),
                     elementos=("elemento_guid", "nunique"))
                .reset_index())

    def elementos_mas_editados(self, top: int = 20) -> pd.DataFrame:
        """Elementos que cambian una y otra vez: casi siempre son un problema de
        datos de origen, no una red que se modifica tanto."""

        if self.df.empty:
            return pd.DataFrame()
        return (self.df.groupby("elemento_guid")
                .agg(cambios=("cambio_id", "count"),
                     capa=("capa", "first"),
                     ultimo=("ocurrido_en", "max"))
                .reset_index().sort_values("cambios", ascending=False).head(top))
