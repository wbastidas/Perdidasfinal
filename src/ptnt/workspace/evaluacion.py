"""Evaluar un escenario: aplicar sus cambios sobre una copia y calcular.

Lo que hace posible «quiero ver ahora cómo sale el balance con mis cambios».

**La copia no es un detalle de implementación, es el mecanismo.** Los cambios se
aplican sobre una copia profunda del modelo migrado; el modelo oficial no se
toca en ningún momento. Si el analista prueba una configuración de banco que
resulta un disparate, lo ve en su número y no ha publicado nada.

**Un cambio que no se pudo aplicar se reporta.** Si el elemento ya no está en la
red —porque una carga posterior lo eliminó, o porque el guid venía mal— el
usuario tiene que enterarse. Callarlo sería peor que fallar: creería que probó
algo que en realidad no se probó, y decidiría sobre eso.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

import pandas as pd
from loguru import logger


@dataclass
class CambioNoAplicado:
    capa: str
    elemento_guid: str
    campo: str
    motivo: str

    def to_dict(self) -> dict:
        return {"capa": self.capa, "elemento": self.elemento_guid,
                "campo": self.campo, "motivo": self.motivo}


@dataclass
class ResultadoEvaluacion:
    """Lo que salió de evaluar un escenario."""

    metricas: dict = field(default_factory=dict)
    por_alimentador: pd.DataFrame = field(default_factory=pd.DataFrame)
    aplicados: int = 0
    no_aplicados: list[CambioNoAplicado] = field(default_factory=list)
    hash_topologia: str = ""
    advertencias: list[str] = field(default_factory=list)
    controles: list[str] = field(default_factory=list)

    @property
    def confiable(self) -> bool:
        """¿Se aplicaron todos los cambios que se querían probar?"""

        return not self.no_aplicados

    @property
    def verificable(self) -> bool:
        """¿Es esta PNT contrastable contra una medición de cabecera?

        Distinto de ``confiable``: los cambios pueden haberse aplicado todos y
        aun así la PNT ser INDICATIVA —estimada—, que es un número para orientar
        el trabajo, no para firmarlo.
        """

        tipo = self.metricas.get("tipo_balance", "")
        return tipo == "MEDIDO"

    def lectura(self) -> str:
        if not self.metricas:
            return "La evaluación no produjo métricas."
        pnt = self.metricas.get("pnt_pct")
        base = (f"PNT {pnt:.2f} %" if isinstance(pnt, (int, float))
                else "sin PNT calculada")
        txt = (f"{base} sobre {self.metricas.get('alimentadores', 0)} "
               f"alimentador(es), con {self.aplicados} cambio(s) aplicados.")
        if not self.verificable:
            txt += (f" Balance {self.metricas.get('tipo_balance', 'SIN_DATOS')}: "
                    "sin medición de cabecera la PNT es una estimación. Sirve "
                    "para comparar una iteración con otra —la estimación es la "
                    "misma en ambas—, no para declararla como pérdida real.")
        if self.no_aplicados:
            txt += (f" ATENCIÓN: {len(self.no_aplicados)} cambio(s) NO se "
                    "pudieron aplicar — este número no refleja todo lo que "
                    "quería probar.")
        return txt


# --------------------------------------------------------------------------- #
# Aplicación de los cambios sobre el modelo
# --------------------------------------------------------------------------- #
# Dónde vive cada capa dentro del modelo de red. Se declara en un mapa y no con
# condicionales repartidos porque añadir una capa nueva debe ser una línea aquí,
# no una cacería por el módulo.
_UBICACION = {
    "ptnt_puesto_transformacion": ("transformer_sites", "site_id"),
    "ptnt_cliente": ("customer_nodes", "guid"),
    "ptnt_luminaria": ("streetlight_nodes", "guid"),
    "ptnt_seccionador": ("switch_nodes", "switch_id"),
    "ptnt_capacitor": ("capacitor_nodes", "cap_id"),
    "ptnt_poste": ("pole_nodes", "pole_id"),
}

# Claves alternativas por las que puede venir identificado un elemento. El campo
# canónico del modelo no siempre coincide con el que trae el paquete de campo.
_CLAVES = ("guid", "site_id", "switch_id", "cap_id", "pole_id",
           "contract_account", "cuenta_contrato", "id")


def aplicar_cambios(model, cambios: list) -> tuple[object, int, list[CambioNoAplicado]]:
    """Devuelve ``(copia_con_cambios, aplicados, no_aplicados)``.

    El modelo original **no se modifica**: se trabaja sobre una copia profunda.
    Cuesta memoria y es exactamente lo que se está comprando — poder equivocarse
    sin consecuencias.
    """

    copia = copy.deepcopy(model)
    aplicados = 0
    fallidos: list[CambioNoAplicado] = []

    for cam in cambios:
        capa = getattr(cam, "capa", "")
        guid = str(getattr(cam, "elemento_guid", "") or "")
        campo = getattr(cam, "campo", "") or ""
        valor = getattr(cam, "valor_despues", None)
        operacion = getattr(cam, "operacion", "MODIFICAR")

        if operacion not in ("MODIFICAR", "RECONECTAR"):
            # Crear y eliminar elementos cambia la topología y exige rehacer el
            # grafo: se deja fuera a propósito en vez de simularlo a medias.
            fallidos.append(CambioNoAplicado(
                capa, guid, campo,
                f"la operación {operacion} no se puede evaluar en un escenario; "
                "aplíquela por la vía de revisión y vuelva a migrar la red"))
            continue

        destino = _UBICACION.get(capa)
        if destino is None:
            fallidos.append(CambioNoAplicado(
                capa, guid, campo, f"capa desconocida para la evaluación: {capa}"))
            continue

        contenedor = getattr(copia, destino[0], None) or {}
        objetivo = _buscar(contenedor, guid)
        if objetivo is None:
            fallidos.append(CambioNoAplicado(
                capa, guid, campo,
                "el elemento no está en la red migrada — puede haberse "
                "eliminado en una carga posterior, o el identificador ser de "
                "otra versión"))
            continue

        if not campo:
            fallidos.append(CambioNoAplicado(
                capa, guid, campo, "el cambio no indica qué campo modificar"))
            continue

        objetivo[campo] = valor
        aplicados += 1

    return copia, aplicados, fallidos


def _buscar(contenedor: dict, guid: str) -> dict | None:
    """Encuentra el diccionario del elemento dentro de la estructura del modelo.

    El modelo guarda unos elementos como ``nodo -> dict`` y otros como
    ``nodo -> [dict, …]``; se recorren los dos casos.
    """

    if not guid:
        return None
    for valor in contenedor.values():
        candidatos = valor if isinstance(valor, list) else [valor]
        for c in candidatos:
            if not isinstance(c, dict):
                continue
            for clave in _CLAVES:
                if str(c.get(clave, "")) == guid:
                    return c
    return None


# --------------------------------------------------------------------------- #
# Evaluación completa
# --------------------------------------------------------------------------- #
_PRECEDENCIA_BALANCE = {"MEDIDO": 0, "INDICATIVO": 1, "PARCIAL": 2, "SIN_DATOS": 3}


def _peor_balance(tipos) -> str:
    """El tipo de balance menos garantista de los presentes."""

    presentes = [str(t) for t in tipos if str(t)]
    if not presentes:
        return "SIN_DATOS"
    return max(presentes, key=lambda t: _PRECEDENCIA_BALANCE.get(t, 9))


def energia_cabecera(ruta) -> dict[str, float]:
    """Lee ``feeder_code,period,kwh_delivered`` y devuelve kWh por alimentador.

    Sin cabecera el balance es INDICATIVO y la PNT una estimación. Con ella es
    MEDIDO y contrastable: la diferencia entre orientar el trabajo y poder
    firmar el número.
    """

    from pathlib import Path

    ruta = Path(ruta)
    if not ruta.exists():
        raise FileNotFoundError(f"No existe el CSV de cabecera: {ruta}")
    df = pd.read_csv(ruta)
    faltan = [c for c in ("feeder_code", "kwh_delivered") if c not in df.columns]
    if faltan:
        raise ValueError(
            f"El CSV de cabecera no tiene la(s) columna(s): {', '.join(faltan)}. "
            "Se esperan 'feeder_code', 'period' y 'kwh_delivered'.")
    # Varios períodos del mismo alimentador se suman: el balance del escenario
    # cubre todo lo cargado, no un mes suelto.
    agg = df.groupby(df["feeder_code"].astype(str))["kwh_delivered"].sum()
    return {k: float(v) for k, v in agg.items()}


def evaluar_escenario(escenario, cambios: list, cfg, *,
                      trifasico: bool = True,
                      cargar_red=None,
                      cabecera: dict[str, float] | None = None,
                      ) -> ResultadoEvaluacion:
    """Calcula el balance del alcance del escenario con sus cambios aplicados.

    ``cargar_red(feeder_code)`` permite inyectar una red ya cargada —lo usa la
    demostración y las pruebas—; por defecto se migra desde la fuente
    configurada, que es lo que ocurre en operación.

    ``cabecera`` es la energía medida en la cabecera de cada alimentador. Con
    ella el balance es MEDIDO; sin ella, INDICATIVO, y así se declara.
    """

    from ptnt.grid_pipeline import run_grid_analysis

    res = ResultadoEvaluacion()
    alimentadores = list(escenario.alimentadores) or [escenario.entidad]

    if cargar_red is None:
        from ptnt.io.migration import migrate_network

        def cargar_red(codigo):
            return migrate_network(cfg, feeder_code=codigo)

    if cabecera:
        sin_medicion = [c for c in alimentadores if c not in cabecera]
        if sin_medicion:
            # Se entregó cabecera, pero no de todos. Callarlo dejaría un
            # consolidado mitad medido y mitad estimado con aspecto de medido.
            res.advertencias.append(
                f"{len(sin_medicion)} alimentador(es) sin energía de cabecera "
                f"({', '.join(sin_medicion[:5])}): su balance será INDICATIVO y "
                "arrastra al del conjunto.")

    filas = []
    hashes = []
    for codigo in alimentadores:
        try:
            red = cargar_red(codigo)
        except Exception as exc:                                # noqa: BLE001
            # Un alimentador que no se puede cargar no cancela la evaluación de
            # los demás: en una subestación de doce, perder el informe entero
            # por uno sería desproporcionado. Pero consta.
            res.advertencias.append(
                f"No se pudo cargar '{codigo}': {type(exc).__name__}: {exc}")
            continue

        # Solo los cambios de este alimentador. Aplicar los de otro produciría
        # «no aplicados» que no lo son.
        propios = [c for c in cambios
                   if not getattr(c, "feeder_code", "") or
                   getattr(c, "feeder_code", "") == codigo]
        con_cambios, aplicados, fallidos = aplicar_cambios(red, propios)
        res.aplicados += aplicados
        res.no_aplicados.extend(fallidos)

        try:
            r = run_grid_analysis(
                con_cambios, cfg, trifasico=trifasico,
                head_energy_kwh=(cabecera or {}).get(codigo))
        except Exception as exc:                                # noqa: BLE001
            res.advertencias.append(
                f"El cálculo de '{codigo}' falló: {type(exc).__name__}: {exc}")
            continue

        b = r.balance
        filas.append({
            "alimentador": codigo,
            "e_input_kwh": b.e_input_kwh,
            "e_billed_kwh": b.e_billed_kwh,
            "perdida_tecnica_kwh": b.loss_technical_kwh,
            "pnt_kwh": b.ntl_kwh,
            "pnt_pct": b.ntl_pct,
            "tipo_balance": b.balance_type.value,
            "converge": r.powerflow_converged,
            "v_min_pu": r.v_min_pu,
        })

        # Las advertencias del balance y los controles que saltaron viajan con
        # el resultado. Un escenario existe para decidir sobre su número: dar la
        # PNT sin decir que es INDICATIVA, o callar que saltó C01 (PNT negativa),
        # sería presentar como comparable algo que no lo es.
        for w in b.warnings:
            res.advertencias.append(f"{codigo}: {w}")
        for c in b.controls:
            if c.triggered:
                res.controles.append(f"{codigo} · {c.code}: {c.detail}")
        h = getattr(r, "hash_topologia", "") or getattr(r, "topology_hash", "")
        if h:
            hashes.append(str(h))

    if not filas:
        res.advertencias.append(
            "Ningún alimentador del alcance pudo evaluarse: no hay resultado.")
        return res

    df = pd.DataFrame(filas)
    res.por_alimentador = df
    entrada = float(df["e_input_kwh"].sum())
    res.metricas = {
        "alimentadores": len(df),
        "e_input_kwh": round(entrada, 1),
        "e_billed_kwh": round(float(df["e_billed_kwh"].sum()), 1),
        "perdida_tecnica_kwh": round(float(df["perdida_tecnica_kwh"].sum()), 1),
        "pnt_kwh": round(float(df["pnt_kwh"].sum()), 1),
        # El porcentaje se RECALCULA sobre los totales, no se promedia: promediar
        # porcentajes de alimentadores de tamaños distintos da un número que no
        # es el de nadie.
        "pnt_pct": round(100.0 * float(df["pnt_kwh"].sum()) / entrada, 3)
        if entrada > 0 else 0.0,
        "convergen": int(df["converge"].sum()),
        "v_min_pu": round(float(df["v_min_pu"].min()), 4),
        # El peor tipo de balance de los alimentadores manda en el conjunto: si
        # uno solo es INDICATIVO, el número de la subestación no es verificable.
        # La energía se suma; la garantía de que es contrastable, no.
        "tipo_balance": _peor_balance(df["tipo_balance"]),
    }
    res.hash_topologia = "|".join(sorted(hashes))[:64]

    if int(df["converge"].sum()) < len(df):
        res.advertencias.append(
            f"{len(df) - int(df['converge'].sum())} alimentador(es) no "
            "convergieron: su pérdida técnica no es de fiar y arrastra la PNT.")
    if res.no_aplicados:
        logger.warning("escenario {}: {} cambio(s) no aplicados",
                       escenario.nombre, len(res.no_aplicados))
    return res
