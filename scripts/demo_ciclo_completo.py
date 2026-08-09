"""Ciclo completo PTNT-BAL: del padrón sintético a la red corregida.

Recorre **todo** el proceso de punta a punta, sin saltarse ninguna pieza y sin
datos preparados de antemano: cada etapa consume lo que produjo la anterior.

     1. Generación del escenario ficticio (comercial, cabecera, red, multados)
     2. Versionado de la red y análisis comercial
     3. Balance de red y pérdidas no técnicas
     4. Diagnóstico de credibilidad del balance
     5. Focalización: dónde ir a inspeccionar
     6. Trabajo adicional definido a mano (censo)
     7. Alta de cuadrillas y reparto de la jornada
     8. Generación de los paquetes descargables
     9. Descarga desde la app móvil (API real, tres técnicos a la vez)
    10. LA JORNADA DE CAMPO: editar, mover, reconectar, fotografiar
    11. Subida y validación del trabajo
    12. Revisión del supervisor
    13. Qué hay que recalcular y por qué
    14. Segundo día: el trabajo que sigue abierto
    15. Verificación final de coherencia

Uso:  python scripts/demo_ciclo_completo.py
"""

from __future__ import annotations

import json
import shutil
import sys
import threading
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from ptnt.config.loader import load_config  # noqa: E402

TECNICOS = [("ana", "Ana Vera"), ("beto", "Beto Ruiz"), ("carla", "Carla Mora")]
CLAVE = "campo-2026-cnel"

_fallos: list[str] = []


def titulo(n: int, texto: str) -> None:
    print(f"\n{'=' * 78}\n  ETAPA {n}. {texto}\n{'=' * 78}")


def sub(texto: str) -> None:
    print(f"\n── {texto} " + "─" * max(0, 74 - len(texto)))


def comprobar(condicion: bool, descripcion: str) -> bool:
    """Cada etapa verifica lo suyo: una demo que solo imprime no demuestra nada."""

    print(f"   {'✓' if condicion else '✗'} {descripcion}")
    if not condicion:
        _fallos.append(descripcion)
    return condicion


def main() -> int:  # noqa: C901 - es un recorrido lineal, se lee como tal
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)

    cfg = load_config(str(RAIZ / "config" / "base.yaml"))
    salidas = RAIZ / "outputs" / "ciclo"
    if salidas.exists():
        shutil.rmtree(salidas)
    salidas.mkdir(parents=True, exist_ok=True)
    cfg.rutas.salidas = str(salidas)
    dir_campo = salidas / "campo"

    # ====================================================================== 1
    titulo(1, "ESCENARIO FICTICIO: PADRÓN, CABECERA, RED Y MULTADOS")
    from ptnt.synth.scenario import build_scenario

    esc = build_scenario(salidas / "datos", n_clientes=900,
                         n_transformadores=8, clientes_por_trafo=20)
    for k, v in esc.resumen.items():
        print(f"   {k:.<40} {v}")
    comprobar(esc.resumen.get("clientes", 0) > 0, "padrón sintético generado")

    # ====================================================================== 2
    titulo(2, "ANÁLISIS COMERCIAL: PROMEDIO, POTENCIA, SEGMENTACIÓN Y SOSPECHA")
    from ptnt.pipeline import run_analysis

    com = run_analysis(cfg, str(esc.csv_consumos), persistir=False)
    m = com.metricas
    print(f"   Cuentas × meses ........................ "
          f"{m['n_cuentas']:,} × {m['n_meses']}")
    print(f"   Clasificado por tarifa ................. "
          f"{m.get('segmentacion_cobertura_pct')} %")
    print(f"   Energía en no residenciales ............ "
          f"{m.get('pct_energia_no_residencial')} %")
    print(f"   Clientes sospechosos ................... {m['n_sospechosos']:,}")
    comprobar(len(com.ranking) > 0, "ranking de sospecha calculado")
    comprobar(m.get("segmentacion_cobertura_pct", 0) > 90,
              "la segmentación clasificó casi todo el padrón")

    # ====================================================================== 3
    titulo(3, "BALANCE DE RED Y PÉRDIDAS NO TÉCNICAS")
    from ptnt.grid_pipeline import run_grid_analysis

    grid = run_grid_analysis(esc.red, cfg, head_energy_kwh=esc.head_energy_kwh,
                             trifasico=True)
    b = grid.balance
    print(f"   Motor .................................. {grid.engine} "
          f"(converge={grid.powerflow_converged})")
    print(f"   Entrada (cabecera) ..................... {b.e_input_kwh:12,.0f} kWh")
    print(f"   − Facturado ............................ {b.e_billed_kwh:12,.0f} kWh")
    print(f"   − Alumbrado no medido .................. "
          f"{b.e_streetlight_unmetered_kwh:12,.0f} kWh")
    print(f"   = Pérdidas totales ..................... {b.loss_total_kwh:12,.0f} kWh")
    print(f"   − Pérdidas técnicas .................... "
          f"{b.loss_technical_kwh:12,.0f} kWh "
          f"(P10–P90 {grid.loss_technical_p10:,.0f}–{grid.loss_technical_p90:,.0f})")
    print(f"   = PNT .................................. {b.ntl_kwh:12,.0f} kWh "
          f"({b.ntl_pct:.1f} %)")
    comprobar(grid.powerflow_converged, "el flujo de potencia converge")
    comprobar(b.ntl_kwh >= 0, "la PNT no es negativa")

    # ====================================================================== 4
    titulo(4, "DIAGNÓSTICO: ¿SE PUEDE CREER ESTE BALANCE?")
    from ptnt.anomalies import (
        analyze_feeder_coherence, analyze_unmatched_customers, detect_transfers,
    )

    tr = detect_transfers(pd.read_csv(esc.csv_cabecera))
    print(f"   Transferencias entre alimentadores ..... {tr.status.value}")
    if tr.candidates:
        c = tr.candidates[0]
        acierto = ({c.feeder_a, c.feeder_b}
                   == {esc.transferencia["origen"], esc.transferencia["destino"]})
        print(f"     detectada {c.feeder_a}→{c.feeder_b} · "
              f"inyectada {esc.transferencia['origen']}→"
              f"{esc.transferencia['destino']}")
        comprobar(acierto, "se detectó exactamente la transferencia inyectada")

    base_cli = com.clientes.merge(com.promedios, on="contract_account", how="left")
    unm = analyze_unmatched_customers(base_cli, pd.read_csv(esc.csv_sig, dtype=str))
    ru = unm.resumen()
    print(f"   Energía vinculada al SIG ............... "
          f"{ru['pct_energia_vinculada']} %  ← techo de calidad del balance")
    print(f"   Clientes sin ubicar .................... {ru['n_csv_sin_sig']:,} "
          f"(inyectados {len(esc.clientes_sin_sig)})")
    comprobar(ru["n_csv_sin_sig"] == len(esc.clientes_sin_sig),
              "se detectaron todos los clientes sin SIG")

    coh = analyze_feeder_coherence([b], transfer_report=tr, unmatched_report=unm)
    df_coh = coh.to_dataframe()
    print(f"   Incoherencias que impiden publicar ..... "
          f"{0 if df_coh.empty else len(df_coh)}")
    print("\n   La PNT de un alimentador con transferencia no reportada no es")
    print("   hurto: es un error de medición. Por eso se diagnostica ANTES de")
    print("   mandar cuadrillas a la calle.")

    # ====================================================================== 5
    titulo(5, "FOCALIZACIÓN: DÓNDE IR A INSPECCIONAR")
    from ptnt.survey.collect import (
        bind_commercial_customers, collect_branch_stats,
        collect_transformer_stats, collect_zone_stats,
    )
    from ptnt.survey.locations import LocationRegistry, register_plan
    from ptnt.survey.routes import analyze_commercial_routes, routes_to_target_input
    from ptnt.survey.targeting import build_survey_plan
    from ptnt.topology.graph import build_radial_graph

    coords = com.clientes[["contract_account", "x", "y"]].copy()
    coords["contract_account"] = coords["contract_account"].astype(str)
    bind_commercial_customers(
        esc.red, sorted(com.ranking["contract_account"].astype(str)), coords)
    graph = build_radial_graph(esc.red)

    umbral = com.ranking["score"].quantile(0.90)
    suspect = set(com.ranking[com.ranking["score"] >= umbral]
                  ["contract_account"].astype(str))
    recuperable = dict(zip(com.ranking["contract_account"].astype(str),
                           com.ranking["recuperable_kwh_mes"]))
    rutas = analyze_commercial_routes(base_cli, com.consumo,
                                      suspect_customers=suspect,
                                      recoverable_by_customer=recuperable,
                                      cuentas_sin_sig=set(esc.clientes_sin_sig))
    plan = build_survey_plan(
        feeder_balances=[{
            "feeder_code": grid.feeder_code, "ntl_kwh": b.ntl_kwh,
            "ntl_pct": b.ntl_pct,
            "customers": sum(len(x) for x in esc.red.customer_nodes.values()),
            "network_km": graph.total_length_km(), "confidence": 100.0,
            "balance_type": b.balance_type.value}],
        zone_signals=collect_zone_stats(graph),
        branch_stats=collect_branch_stats(graph, suspect_customers=suspect,
                                          recoverable_by_customer=recuperable),
        transformer_stats=collect_transformer_stats(
            graph, grid.lv_zones, grid.transformer_loading,
            suspect_customers=suspect, recoverable_by_customer=recuperable),
        route_stats=routes_to_target_input(rutas),
        customer_ranking=com.ranking, customer_coords=coords,
    )
    print(f"   Objetivos priorizados .................. "
          f"{plan.resumen['n_objetivos']:,}")
    print(f"   Por nivel .............................. {plan.resumen['por_nivel']}")

    ordenes_pnt = plan.work_orders(top_n=12)
    print()
    print("   " + ordenes_pnt[["orden_trabajo", "nivel", "entidad",
                               "clientes_a_revisar", "kwh_por_visita"]]
          .head(6).to_string(index=False).replace("\n", "\n   "))
    comprobar(not ordenes_pnt.empty, "hay órdenes de inspección priorizadas")

    registro_ubic = LocationRegistry(salidas / "ubicaciones.json")
    n_reg = register_plan(plan, registro_ubic)
    registro_ubic.save()
    print(f"\n   Ubicaciones con identidad geográfica estable: {n_reg:,}")
    print("   Sobreviven a la carga de datos nuevos: un sector priorizado hoy")
    print("   sigue siendo el mismo sector el mes que viene.")

    # ====================================================================== 6
    titulo(6, "TRABAJO DEFINIDO A MANO: UN CENSO QUE EL RANKING NO PIDE")
    from ptnt.field import TipoTrabajo, desde_plan, por_alimentador, unir

    padron = com.clientes.copy()
    padron["alimentador"] = grid.feeder_code
    padron = padron.rename(columns={"contract_account": "cuenta_contrato"})
    censo = por_alimentador(padron, [grid.feeder_code], tipo=TipoTrabajo.CENSO,
                            clientes_por_orden=120, prefijo="CEN")
    print(f"   Censo de {grid.feeder_code}: {len(censo.ordenes)} orden(es), "
          f"{censo.clientes:,} clientes")
    for a in censo.advertencias:
        print(f"   ⚠ {a}")
    print("\n   Un censo no recupera kWh: corrige el DENOMINADOR del balance.")
    print("   Evaluarlo por energía lo haría parecer inútil y dejaría de hacerse.")
    comprobar(float(censo.ordenes["recuperable_kwh_mes"].sum()) == 0.0,
              "el censo no se evalúa por energía recuperada")

    ordenes_pnt = ordenes_pnt.rename(columns={"alimentador": "alimentador"})
    if "alimentador" not in ordenes_pnt.columns:
        ordenes_pnt["alimentador"] = grid.feeder_code
    todas = unir(desde_plan(ordenes_pnt), censo)
    ruta_ordenes = salidas / "ordenes_todas.csv"
    todas.to_csv(ruta_ordenes, index=False)
    print(f"\n   A repartir: {len(todas)} orden(es) — "
          f"{todas['tipo_trabajo'].value_counts().to_dict()}")
    comprobar(todas["orden_trabajo"].nunique() == len(todas),
              "ninguna orden repetida al unir campañas")

    # ====================================================================== 7
    titulo(7, "CUADRILLAS Y REPARTO DE LA JORNADA")
    from ptnt.field import RegistroCampo, asignar_reparto, repartir_ordenes

    reg = RegistroCampo(dir_campo / "registro.json")
    for usuario, nombre in TECNICOS:
        reg.crear_usuario(usuario, nombre, CLAVE,
                          unidad_negocio=cfg.proyecto.unidad_negocio)
    print(f"   {len(TECNICOS)} técnicos creados en el backend "
          "(nunca en el dispositivo)")

    rep = repartir_ordenes(todas, [u for u, _ in TECNICOS], criterio="kwh")
    print()
    print(rep.resumen().to_string(index=False))
    print(f"\n   Desbalance: {rep.desbalance_pct:.1f} %  ·  dispersión media: "
          f"{rep.resumen()['dispersion_km'].mean():.2f} km")
    for a in rep.advertencias():
        print(f"   ⚠ {a}")
    asignar_reparto(reg, rep, asignado_por="supervisor")
    comprobar(rep.desbalance_pct < 30.0, "carga repartida sin desbalance grave")
    comprobar(len(reg.asignaciones) == len(todas),
              "todas las órdenes quedaron asignadas")

    # ====================================================================== 8
    titulo(8, "PAQUETES DESCARGABLES")
    from ptnt.field import construir_paquetes, resumen_paquetes
    from ptnt.field.demo_red import red_de_demostracion

    capas, conexiones = red_de_demostracion(list(reg.asignaciones.values()))
    resultados = construir_paquetes(
        dir_campo / "paquetes", registro=reg, red=capas, conexiones=conexiones,
        version_red=cfg.proyecto.version_config)
    tabla = resumen_paquetes(resultados)
    print(tabla[["usuario", "estado", "ordenes", "elementos", "area_km2",
                 "tamano_mb"]].to_string(index=False))
    comprobar(all(not isinstance(r, str) for r in resultados.values()),
              "todos los paquetes se generaron")

    # ====================================================================== 9
    titulo(9, "DESCARGA DESDE LA APP (API REAL, LOS TRES A LA VEZ)")
    from fastapi.testclient import TestClient

    from ptnt.field.api import crear_app

    app = crear_app(registro_ruta=dir_campo / "registro.json",
                    paquetes_dir=dir_campo / "paquetes",
                    entrantes_dir=dir_campo / "entrantes",
                    lotes_dir=dir_campo / "lotes")
    cliente = TestClient(app)

    tokens: dict[str, str] = {}
    for usuario, _ in TECNICOS:
        r = cliente.post("/movil/vincular", json={
            "usuario": usuario, "password": CLAVE,
            "dispositivo_id": f"TEL-{usuario.upper()}"})
        r.raise_for_status()
        tokens[usuario] = r.json()["token"]
    print(f"   {len(tokens)} equipos vinculados; el token es revocable sin "
          "tocar la cuenta")

    vistas: dict[str, list[str]] = {}
    for usuario, token in tokens.items():
        r = cliente.get("/movil/ordenes",
                        headers={"Authorization": f"Bearer {token}"})
        vistas[usuario] = [o["orden_trabajo"] for o in r.json()["ordenes"]]
    for usuario, lista in vistas.items():
        ajenas = set(lista) - set(rep.por_usuario[usuario]["orden_trabajo"])
        comprobar(not ajenas, f"{usuario} ve solo su trabajo ({len(lista)} órdenes)")

    barrera = threading.Barrier(len(tokens))
    codigos: dict[str, int] = {}

    def baja(usuario: str, token: str) -> None:
        barrera.wait()
        codigos[usuario] = cliente.get(
            "/movil/paquete",
            headers={"Authorization": f"Bearer {token}"}).status_code

    hilos = [threading.Thread(target=baja, args=(u, t)) for u, t in tokens.items()]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    comprobar(set(codigos.values()) == {200}, "descarga simultánea sin errores")

    estado_tras_descarga = RegistroCampo(dir_campo / "registro.json")
    descargadas = [a for a in estado_tras_descarga.asignaciones.values()
                   if a.estado.value == "DESCARGADA"]
    comprobar(len(descargadas) == len(todas),
              f"{len(descargadas)}/{len(todas)} órdenes DESCARGADA "
              "(ninguna actualización perdida)")

    # ===================================================================== 10
    titulo(10, "LA JORNADA DE CAMPO")
    from ptnt.field.simulator import SimuladorCampo

    print("   Se ejecutan sobre el paquete real las mismas operaciones que hace\n"
          "   la app Android, escribiendo el mismo diario de cambios.\n")

    retornos: dict[str, Path] = {}
    jornadas: dict[str, dict] = {}

    for usuario, _ in TECNICOS:
        origen = dir_campo / "paquetes" / f"{usuario}.gpkg"
        retorno = dir_campo / "retornos" / f"{usuario}_dia1.gpkg"
        retorno.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(origen, retorno)
        retornos[usuario] = retorno

        with SimuladorCampo(retorno, usuario=usuario) as sim:
            mis_ordenes = [o["orden_trabajo"] for o in sim.ordenes()]
            if not mis_ordenes:
                continue

            # -- primera orden: se trabaja y se cierra --------------------
            sim.abrir_orden(mis_ordenes[0])
            clientes = sim.elementos("ptnt_cliente", limite=6)
            puestos = sim.elementos("ptnt_puesto_transformacion")

            for i, c in enumerate(clientes[:3]):
                sim.editar_atributos("ptnt_cliente", str(c["guid"]), {
                    "hallazgo": "MEDIDOR_MANIPULADO" if i == 0 else "NORMAL",
                    "inspeccionado": 1,
                    "lectura_medidor": 12345.0 + i,
                }, motivo="Inspección en sitio")
                sim.fotografiar(str(c["guid"]), "ptnt_cliente",
                                descripcion="Medidor y acometida")

            # Mover un cliente: la acometida debe seguirlo.
            if clientes:
                g = clientes[0]
                from ptnt.field.gpkg import leer_geometria

                geo = leer_geometria(g.get("geom"))
                if geo:
                    x, y = geo["coords"][0]
                    n = sim.mover("ptnt_cliente", str(g["guid"]), x + 9.0, y + 6.0,
                                  motivo="Medidor estaba en la vereda de enfrente")
                    print(f"   {usuario}: mover cliente → {n} elemento(s) "
                          f"({max(0, n - 1)} arrastrados por topología)")

            # Reconectar: la corrección que cambia dos balances.
            if clientes and len(puestos) >= 2:
                actual = str(clientes[1].get("puesto_guid") or "")
                otro = next((str(p["guid"]) for p in puestos
                             if str(p["guid"]) != actual), "")
                if otro and sim.reconectar(str(clientes[1]["guid"]), otro,
                                           motivo="Seguimiento de acometida"):
                    print(f"   {usuario}: reconexión "
                          f"{actual[:8] or '(sin asignar)'} → {otro[:8]}")

            sim.cerrar_orden(mis_ordenes[0], "Hurto confirmado en 1 de 3 predios")

            # -- segunda orden: se empieza y queda abierta ----------------
            if len(mis_ordenes) > 1:
                sim.abrir_orden(mis_ordenes[1])
                restantes = sim.elementos("ptnt_cliente", limite=12)[6:9]
                for c in restantes:
                    sim.editar_atributos("ptnt_cliente", str(c["guid"]),
                                         {"inspeccionado": 1},
                                         motivo="Recorrido parcial")

            jornadas[usuario] = sim.resumen.to_dict()
            for a in sim.resumen.advertencias[:2]:
                print(f"   ⚠ {usuario}: {a}")

    print()
    print(pd.DataFrame(jornadas).T.to_string())
    total_reconexiones = sum(j.get("reconexiones", 0) for j in jornadas.values())
    comprobar(total_reconexiones > 0, "hubo reconexiones de consumidor")
    comprobar(all(j["cerradas"] >= 1 for j in jornadas.values()),
              "cada técnico cerró al menos una orden")

    # ===================================================================== 11
    titulo(11, "SUBIDA Y VALIDACIÓN")
    respuestas: dict[str, dict] = {}
    barrera2 = threading.Barrier(len(tokens))

    def sube(usuario: str, token: str) -> None:
        barrera2.wait()
        with retornos[usuario].open("rb") as f:
            r = cliente.post(
                "/movil/sincronizar",
                headers={"Authorization": f"Bearer {token}"},
                files={"archivo": (f"{usuario}.gpkg", f,
                                   "application/octet-stream")})
        respuestas[usuario] = {"codigo": r.status_code, **r.json()}

    hilos = [threading.Thread(target=sube, args=(u, t)) for u, t in tokens.items()]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    for usuario, r in respuestas.items():
        res = r.get("resumen", {})
        print(f"   {usuario:6s} HTTP {r['codigo']} · {res.get('cambios', 0)} "
              f"cambio(s) · {res.get('fotos', 0)} foto(s) · "
              f"cerradas {len(r.get('ordenes_cerradas', []))} · "
              f"en curso {len(r.get('ordenes_en_curso', []))}")
        for h in r.get("hallazgos", []):
            print(f"          [{h['severidad']}] {h['detalle'][:90]}")

    # El cliente móvil marca lo enviado DESPUÉS del 200 del servidor. Aquí se
    # hace lo mismo: es lo que evita que mañana se reenvíe la jornada de hoy.
    for usuario, r in respuestas.items():
        if r["codigo"] == 200 and not r.get("resumen", {}).get("bloqueado"):
            with SimuladorCampo(retornos[usuario], usuario=usuario) as sim:
                n = sim.marcar_sincronizados(r["lote_id"])
                print(f"   {usuario:6s} {n} cambio(s) marcados como enviados · "
                      f"quedan {sim.pendientes()} pendientes")

    comprobar({r["codigo"] for r in respuestas.values()} == {200},
              "los tres lotes se aceptaron")
    comprobar(len({r["lote_id"] for r in respuestas.values()}) == len(TECNICOS),
              "cada lote tiene su propio identificador")
    comprobar(all(len(r["ordenes_en_curso"]) >= 1 for r in respuestas.values()),
              "las órdenes empezadas siguen abiertas (trabajo de varios días)")

    # ===================================================================== 12
    titulo(12, "REVISIÓN DEL SUPERVISOR")
    from ptnt.field.sync import (
        CambioRecibido, EstadoRevision, HistoricoCambios, LoteSincronizacion,
        aplicar, revisar,
    )

    lotes = []
    for archivo in sorted((dir_campo / "lotes").glob("*.json")):
        d = json.loads(archivo.read_text(encoding="utf-8"))
        lote = LoteSincronizacion(
            lote_id=d["lote_id"], usuario=d.get("usuario", ""),
            paquete_id=d.get("paquete_id", ""),
            recibido_en=d.get("recibido_en", ""),
            fotos=d.get("fotos", []), ordenes=d.get("ordenes", []))
        for c in d.get("cambios", []):
            c = dict(c)
            c["estado_revision"] = EstadoRevision(
                c.get("estado_revision", "PENDIENTE"))
            lote.cambios.append(CambioRecibido(**c))
        lotes.append(lote)

    print(f"   {len(lotes)} lote(s) en revisión")
    muestra = lotes[0]
    df = muestra.to_dataframe()
    print(f"\n   Lote de '{muestra.usuario}' — primeras filas de lo que ve el "
          "supervisor:")
    cols = [c for c in ("secuencia", "capa", "operacion", "campo", "antes",
                        "despues", "propagado", "motivo") if c in df.columns]
    print(df[cols].head(8).to_string(index=False))

    # La revisión es GRANULAR: el supervisor acepta la lectura del medidor y
    # rechaza el movimiento propagado del mismo elemento, si no lo convence.
    # Aceptar todo en bloque convertiría la revisión en un trámite.
    propagados = [c.secuencia for c in muestra.cambios if c.propagado_de]
    a_rechazar = propagados[:1]
    r = revisar(muestra,
                aceptar=[c.secuencia for c in muestra.cambios
                         if c.secuencia not in a_rechazar],
                rechazar=a_rechazar, revisor="supervisor")
    print(f"\n   Aceptados {r['aceptados']} · rechazados {r['rechazados']}"
          + (f" (secuencia {a_rechazar[0]}, arrastre no justificado)"
             if a_rechazar else ""))
    if r["advertencia"]:
        print(f"   ⚠ {r['advertencia']}")
    comprobar(r["rechazados"] == len(a_rechazar),
              "el supervisor puede rechazar un cambio sin rechazar el resto")

    for otro in lotes[1:]:
        revisar(otro, aceptar_todo=True, revisor="supervisor")

    hist = HistoricoCambios(dir_campo / "historico_cambios.parquet")
    n_hist = sum(hist.registrar_lote(x) for x in lotes)
    hist.save()
    print(f"   {n_hist} cambio(s) al histórico permanente")
    comprobar(n_hist > 0, "el histórico registró la jornada")

    # ===================================================================== 13
    titulo(13, "QUÉ HAY QUE RECALCULAR")
    etapas: set[str] = set()
    reconexiones = 0
    for lote in lotes:
        res = aplicar(lote)
        etapas |= set(res.etapas_a_recalcular)
        reconexiones += res.reconexiones
        if res.reconexiones:
            print(f"   {lote.usuario}: {res.detalle[:150]}")

    print(f"\n   Etapas invalidadas: {', '.join(sorted(etapas)) or '(ninguna)'}")
    comprobar(reconexiones > 0, "las reconexiones llegaron al backend")
    comprobar({"topologia", "balance", "focalizacion", "ranking"} <= etapas,
              "una reconexión obliga a rehacer topología, balance y ranking")
    print("\n   Ejecutar:  ptnt analizar-red   y   ptnt focalizar")
    print("   El ranking cambia porque el consumo se movió de zona.")

    # ===================================================================== 14
    titulo(14, "SEGUNDO DÍA: LO QUE QUEDÓ ABIERTO")
    reg2 = RegistroCampo(dir_campo / "registro.json")
    abiertas = [a for a in reg2.asignaciones.values()
                if a.estado.value in ("DESCARGADA", "EN_PROCESO")]
    con_avance = [a for a in abiertas if a.visitas > 0]
    print(f"   Órdenes abiertas ....... {len(abiertas)}")
    print(f"   Con jornada registrada . {len(con_avance)}")
    comprobar(len(con_avance) >= len(TECNICOS),
              "el avance de cada técnico quedó anotado")
    comprobar(all(a.estado.value == "EN_PROCESO" for a in con_avance),
              "las órdenes con avance figuran EN_PROCESO, no «descargadas»")

    usuario = TECNICOS[0][0]
    retorno = retornos[usuario]
    with SimuladorCampo(retorno, usuario=usuario) as sim:
        antes = len(sim.elementos("ptnt_cambio"))
        pendiente = [o for o in sim.ordenes() if o["estado"] == "EN_PROCESO"]
        if pendiente:
            ot = str(pendiente[0]["orden_trabajo"])
            sim.abrir_orden(ot)
            for c in sim.elementos("ptnt_cliente", limite=15)[9:11]:
                sim.editar_atributos("ptnt_cliente", str(c["guid"]),
                                     {"hallazgo": "NORMAL"},
                                     motivo="Segunda jornada")
            sim.cerrar_orden(ot, "Recorrido completado en dos jornadas")
        despues = len(sim.elementos("ptnt_cambio"))
    print(f"   Diario de {usuario}: {antes} → {despues} entradas "
          "(lo de ayer se conserva, marcado como enviado)")

    with retorno.open("rb") as f:
        r2 = cliente.post(
            "/movil/sincronizar",
            headers={"Authorization": f"Bearer {tokens[usuario]}"},
            files={"archivo": ("dia2.gpkg", f, "application/octet-stream")})
    cuerpo2 = r2.json()
    res2 = cuerpo2.get("resumen", {})
    print(f"   Subida del día 2: {res2.get('cambios', 0)} cambio(s) nuevos, "
          f"{res2.get('ya_sincronizados', 0)} ya enviados que NO se reprocesan")
    comprobar(res2.get("ya_sincronizados", 0) > 0,
              "lo de ayer no se contó dos veces")
    comprobar(res2.get("cambios", 0) < despues,
              "solo subió lo nuevo, no el diario completo")

    # ===================================================================== 15
    titulo(15, "VERIFICACIÓN FINAL")
    final = RegistroCampo(dir_campo / "registro.json")
    por_estado = pd.Series(
        [a.estado.value for a in final.asignaciones.values()]).value_counts()
    print("   Estado de las órdenes:")
    print("   " + por_estado.to_string().replace("\n", "\n   "))

    print("\n   Carga por técnico:")
    resumen_u = final.resumen_por_usuario()
    cols = [c for c in ("usuario", "ordenes", "DESCARGADA", "EN_PROCESO",
                        "COMPLETADA", "jornadas") if c in resumen_u.columns]
    print("   " + resumen_u[cols].to_string(index=False).replace("\n", "\n   "))

    print("\n   Bitácora (últimas operaciones):")
    b = final.bitacora(6)
    print("   " + b[["operacion", "actor", "detalle"]]
          .to_string(index=False).replace("\n", "\n   "))

    comprobar(len(final.asignaciones) == len(todas),
              "ninguna orden se perdió en todo el ciclo")
    comprobar(sum(1 for a in final.asignaciones.values()
                  if a.estado.value == "COMPLETADA") >= len(TECNICOS),
              "hay órdenes cerradas con resultado")

    print(f"\n{'=' * 78}")
    if _fallos:
        print(f"  ✗ {len(_fallos)} COMPROBACIÓN(ES) FALLIDA(S):")
        for f in _fallos:
            print(f"      · {f}")
    else:
        print("  ✓ CICLO COMPLETO COHERENTE — todas las comprobaciones pasaron")
    print(f"{'=' * 78}")
    print(f"\n  Salidas en: {salidas}")
    return 1 if _fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
