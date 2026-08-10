"""Reconexión de consumidores, definición del trabajo y jornadas de varios días.

Tres cosas que solo aparecen cuando el sistema se usa de verdad:

* Un cliente colgado del transformador equivocado produce **PNT falsa en dos
  zonas a la vez** —una pierde energía que no consumió, la otra la gana— y los
  dos balances son internamente coherentes, así que ningún análisis de oficina
  lo detecta. Corregirlo es lo que más vale de una visita.
* El trabajo de campo no es solo perseguir hurto. Un censo o una actualización
  cartográfica nunca van a salir de un ranking de sospecha, y son justamente
  los casos donde el balance no es malo sino *incalculable*.
* Una revisión puede llevar días. Si sincronizar cierra la orden, el supervisor
  ve terminada una jornada que va por la mitad.
"""

import pandas as pd
import pytest

from ptnt.field import TipoTrabajo, por_alimentador, por_area, por_lista, unir
from ptnt.field.schema import capas_red, esquema_para_movil
from ptnt.field.sync import (
    CambioRecibido,
    EstadoRevision,
    LoteSincronizacion,
    Severidad,
    aplicar,
    revisar,
)


# --------------------------------------------------------------------------- #
# Reconexión: el modelo lo permite y el recálculo lo entiende
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_la_conexion_del_consumidor_es_editable_en_campo():
    """Era `editable=False`: obligaba a anotarlo en papel y corregirlo después
    en oficina, que en la práctica significa que no se corrige."""

    cliente = next(c for c in capas_red() if c.nombre == "ptnt_cliente")
    campos = {f.nombre: f for f in cliente.campos}
    assert campos["puesto_guid"].editable, "el transformador debe poder corregirse"
    assert campos["unidad_guid"].editable, "la fase del banco también"


@pytest.mark.unit
def test_el_movil_recibe_la_conexion_como_campo_editable():
    """El formulario se arma desde el manifiesto: si llega como no editable, el
    técnico ve el dato pero no puede tocarlo."""

    esquema = esquema_para_movil()
    capa = next(c for c in esquema["capas"] if c["nombre"] == "ptnt_cliente")
    campos = {f["nombre"]: f for f in capa["campos"]}
    assert campos["puesto_guid"]["editable"]
    assert "balance" in campos["puesto_guid"]["ayuda"].lower()


def _lote(cambios: list[CambioRecibido]) -> LoteSincronizacion:
    return LoteSincronizacion(lote_id="L1", usuario="ana", paquete_id="P1",
                              recibido_en="2026-08-09T10:00:00+00:00",
                              cambios=cambios)


def _reconexion(secuencia=1, antes="TX-A", despues="TX-B") -> CambioRecibido:
    return CambioRecibido(
        guid=f"c{secuencia}", secuencia=secuencia, capa="ptnt_cliente",
        elemento_guid="CLI-1", operacion="RECONECTAR", campo="puesto_guid",
        valor_antes=antes, valor_despues=despues, autor="ana",
        ocurrido_en="2026-08-09T09:00:00+00:00",
        motivo="Seguimiento de acometida")


@pytest.mark.unit
def test_reconectar_es_un_cambio_topologico_no_un_atributo():
    """Mover un poste treinta metros no cambia de qué cuelga; reconectar sí,
    aunque no toque un solo píxel del mapa."""

    c = _reconexion()
    assert c.afecta_topologia
    assert c.afecta_dos_zonas
    assert not c.afecta_geometria


@pytest.mark.unit
def test_la_reconexion_obliga_a_recalcular_las_dos_zonas():
    """Recalcular solo la zona del cliente dejaría a la otra con energía
    facturada que ya no le corresponde."""

    lote = _lote([_reconexion()])
    revisar(lote, aceptar_todo=True, revisor="supervisor")
    res = aplicar(lote, feeder_por_elemento={
        "CLI-1": "GYE-04", "TX-A": "GYE-04", "TX-B": "GYE-05"})

    assert res.reconexiones == 1
    # Los dos transformadores entran como afectados, no solo el cliente.
    assert {"CLI-1", "TX-A", "TX-B"} <= res.elementos_afectados
    assert res.alimentadores_afectados == {"GYE-04", "GYE-05"}
    # Y hay que rehacer topología y ranking, no solo el balance.
    assert {"topologia", "balance", "focalizacion", "ranking"} <= set(
        res.etapas_a_recalcular)
    assert "reconexión" in res.detalle


@pytest.mark.unit
def test_una_reconexion_sin_origen_o_destino_bloquea_el_lote():
    """Sin ambos extremos no se sabe de qué zona sale el consumo ni a cuál
    entra, que es justamente el dato que importa."""

    lote = _lote([_reconexion(antes="TX-A", despues=None)])
    from ptnt.field.sync import _validar

    codigos = {h.codigo for h in _validar(lote)}
    assert "SYNC17" in codigos
    bloqueantes = [h for h in _validar(lote)
                   if h.severidad is Severidad.BLOQUEANTE]
    assert bloqueantes


@pytest.mark.unit
def test_reconectar_al_mismo_transformador_solo_advierte():
    """Es un toque accidental, no una corrección: se avisa pero no se bloquea el
    trabajo del día por eso."""

    from ptnt.field.sync import _validar

    lote = _lote([_reconexion(antes="TX-A", despues="TX-A")])
    hallazgos = _validar(lote)
    assert any(h.codigo == "SYNC18" for h in hallazgos)
    assert not any(h.severidad is Severidad.BLOQUEANTE for h in hallazgos)


@pytest.mark.unit
def test_un_cambio_rechazado_no_arrastra_recalculo():
    """El supervisor rechaza la reconexión: nada debe recalcularse por ella."""

    lote = _lote([_reconexion()])
    revisar(lote, rechazar=[1], revisor="supervisor")
    res = aplicar(lote)
    assert res.reconexiones == 0
    assert res.etapas_a_recalcular == []
    assert lote.cambios[0].estado_revision is EstadoRevision.RECHAZADO


# --------------------------------------------------------------------------- #
# Definir el trabajo: no solo lo peor del ranking
# --------------------------------------------------------------------------- #
def _padron(n=120) -> pd.DataFrame:
    import numpy as np

    rng = np.random.default_rng(5)
    centros = {"GYE-04": (630000.0, 9760000.0), "GYE-05": (636000.0, 9764000.0)}
    filas = []
    for i in range(n):
        f = "GYE-04" if i % 2 else "GYE-05"
        cx, cy = centros[f]
        filas.append({
            "cuenta_contrato": f"{100000 + i}", "alimentador": f,
            "x": cx + float(rng.normal(0, 400)),
            "y": cy + float(rng.normal(0, 400)),
            "recuperable_kwh_mes": float(rng.integers(0, 900)),
        })
    return pd.DataFrame(filas)


@pytest.mark.unit
def test_un_censo_no_se_evalua_por_energia_recuperada():
    """Un censo corrige el denominador del balance, no lo recupera. Ponerle un
    número de kWh lo haría parecer inútil y dejaría de hacerse."""

    d = por_alimentador(_padron(), ["GYE-04"], tipo=TipoTrabajo.CENSO)
    assert not d.tipo.mide_energia
    assert d.ordenes["recuperable_kwh_mes"].sum() == 0
    assert d.clientes == 60


@pytest.mark.unit
def test_el_trabajo_se_parte_en_bloques_compactos():
    """Una orden con 40 clientes repartidos por todo el alimentador es una orden
    que no se hace: el traslado se come la jornada."""

    import numpy as np

    d = por_alimentador(_padron(200), ["GYE-04", "GYE-05"],
                        tipo=TipoTrabajo.CENSO, clientes_por_orden=25)
    assert len(d.ordenes) >= 4
    # Cada orden debe caer dentro de uno de los dos barrios, no a caballo.
    for _, r in d.ordenes.iterrows():
        d04 = np.hypot(r["x"] - 630000, r["y"] - 9760000)
        d05 = np.hypot(r["x"] - 636000, r["y"] - 9764000)
        assert min(d04, d05) < 1500, (r["entidad"], d04, d05)


@pytest.mark.unit
def test_las_cuentas_que_no_estan_en_el_padron_se_reportan():
    """Suelen venir de otro sistema o de otra unidad de negocio. Descartarlas en
    silencio manda a la cuadrilla a una dirección que no existe."""

    d = por_lista(_padron(), ["100001", "100003", "999999"],
                  tipo=TipoTrabajo.VERIFICACION_MEDIDOR)
    assert d.clientes == 2
    assert any("999999" in a for a in d.advertencias)


@pytest.mark.unit
def test_seleccion_por_area_dibujada():
    """Es como un jefe de zona describe el trabajo cuando el criterio no está en
    ningún dato: una urbanización nueva, una zona tras un temporal."""

    d = por_area(_padron(), x=630000, y=9760000, radio_m=500,
                 tipo=TipoTrabajo.ACTUALIZACION_CARTOGRAFICA)
    assert not d.ordenes.empty
    assert d.clientes > 0
    assert (d.ordenes["nivel"] == "AREA").all()


@pytest.mark.unit
def test_unir_campanas_no_deja_ordenes_con_el_mismo_codigo():
    """Dos campañas generadas por separado empiezan las dos en 0001, y dos
    órdenes con el mismo código son imposibles de seguir después."""

    a = por_alimentador(_padron(), ["GYE-04"], tipo=TipoTrabajo.CENSO)
    b = por_area(_padron(), x=636000, y=9764000, radio_m=800)
    u = unir(a, b)
    assert len(u) == len(a.ordenes) + len(b.ordenes)
    assert u["orden_trabajo"].nunique() == len(u)


@pytest.mark.unit
def test_area_sin_clientes_lo_dice_en_vez_de_devolver_vacio():
    d = por_area(_padron(), x=0, y=0, radio_m=100)
    assert d.ordenes.empty
    assert d.advertencias and "Ningún cliente" in d.advertencias[0]
