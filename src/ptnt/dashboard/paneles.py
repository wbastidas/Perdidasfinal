"""Paneles del tablero: ejecutar, programar y probar cambios.

Hasta ahora el tablero solo **miraba** resultados: para calcular algo había que
abrir una consola y escribir un comando. Eso deja el sistema en manos de quien
sabe de informática, no de quien sabe de pérdidas.

Estos paneles cierran esa distancia. La regla que los gobierna: **quien los usa
no tiene por qué saber qué es un alimentador ni qué hace un flujo de potencia.**
Cada botón dice lo que va a pasar, cuánto tarda y qué produce; cada error se
explica en una frase y con el siguiente paso a dar.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

_COLOR = {"CORRECTO": "🟢", "FALLO": "🔴", "EN_CURSO": "🔵",
          "OMITIDO": "⚪", "PENDIENTE": "⚪"}


# --------------------------------------------------------------------------- #
# Ejecutar el proceso
# --------------------------------------------------------------------------- #
def panel_ejecucion(cfg, ruta_config: str, usuario: str) -> None:
    from ptnt.jobs import (PASOS, PLANES, Bitacora, duracion_estimada,
                           lanzar_en_segundo_plano, resolver_plan,
                           supuestos_previos)

    st.subheader("Actualizar los números")
    st.caption("Aquí se lanza el cálculo. No hace falta escribir ningún comando: "
               "elija qué quiere hacer y pulse el botón.")

    bit = Bitacora(Path(cfg.rutas.salidas) / "ejecuciones")
    en_curso = bit.en_curso()

    # -- Si hay algo corriendo, lo primero es enseñarlo -----------------------
    if en_curso:
        _mostrar_en_curso(en_curso)
        return

    izq, der = st.columns([2, 1])

    with izq:
        st.markdown("#### 1. ¿Qué quiere hacer?")
        etiquetas = {c: f"{t} — {e}" for c, (t, _p, e) in PLANES.items()}
        etiquetas["_manual"] = "Elegir los pasos uno a uno (avanzado)"
        eleccion = st.radio("Elija una opción", list(etiquetas),
                            format_func=lambda c: etiquetas[c],
                            label_visibility="collapsed")

        if eleccion == "_manual":
            claves = st.multiselect(
                "Pasos a ejecutar",
                [p.clave for p in PASOS],
                format_func=lambda c: next(p.titulo for p in PASOS if p.clave == c))
            nombre_plan = "pasos elegidos"
        else:
            claves = list(PLANES[eleccion][1])
            nombre_plan = PLANES[eleccion][0]

        if not claves:
            st.info("Elija al menos un paso para continuar.")
            return

        pasos = resolver_plan(claves)
        st.markdown("#### 2. Esto es lo que va a ocurrir")
        for i, p in enumerate(pasos, 1):
            st.markdown(f"**{i}. {p.titulo}** — {p.para_que}")
        for aviso in supuestos_previos(claves):
            st.warning(aviso)

    with der:
        st.markdown("#### 3. Datos de entrada")
        st.caption("Solo si los pasos elegidos los necesitan.")
        csv = st.text_input("Archivo de consumos (CSV)", "",
                            help="La historia de consumo de los clientes.")
        cabecera = st.text_input("Medición de cabecera (CSV)", "",
                                 help="Sin esto, la pérdida no técnica será una "
                                      "estimación y no un número verificable.")
        multados = st.text_input("Base de multados (CSV)", "",
                                 help="Casos de hurto ya confirmados. Sirve para "
                                      "medir si el detector acierta.")
        feeder = st.text_input("Un solo alimentador", "",
                               help="Deje vacío para procesar todo.")

        st.metric("Tiempo estimado", f"{duracion_estimada(pasos)} min")
        st.caption("Puede cerrar esta ventana: el cálculo sigue su curso.")

        if st.button("▶️  Empezar", type="primary", use_container_width=True):
            ident = lanzar_en_segundo_plano(
                claves, ruta_config, usuario=usuario, nombre_plan=nombre_plan,
                csv=csv, feeder=feeder, cabecera=cabecera, multados=multados)
            st.session_state["ejecucion_actual"] = ident
            st.rerun()

    st.divider()
    _historial(bit)


def _mostrar_en_curso(ej: dict) -> None:
    st.info(f"**{ej.get('lectura', 'En curso…')}**")
    hechos = sum(1 for p in ej["pasos"] if p["estado"] == "CORRECTO")
    st.progress(hechos / max(1, len(ej["pasos"])))

    for p in ej["pasos"]:
        icono = _COLOR.get(p["estado"], "⚪")
        detalle = f" — {p['resumen']}" if p.get("resumen") else ""
        st.markdown(f"{icono} **{p['titulo']}**{detalle}")

    st.caption(f"Empezó a las {ej.get('inicio', '')[11:16]}. "
               "Esta pantalla no se actualiza sola: pulse el botón para ver "
               "cómo va.")
    if st.button("🔄  Actualizar"):
        st.rerun()


def _historial(bit) -> None:
    st.markdown("#### Últimas veces que se ejecutó")
    ultimas = bit.ultimas(10)
    if not ultimas:
        st.caption("Todavía no se ha ejecutado nada.")
        return

    filas = [{
        "Cuándo": e.get("inicio", "").replace("T", " ")[:16],
        "Qué": e.get("plan", ""),
        "Quién": e.get("usuario") or ("automática" if e.get("origen") ==
                                      "PROGRAMADA" else "—"),
        "Cómo acabó": f"{_COLOR.get(e.get('estado'), '⚪')} {e.get('estado', '')}",
        "Detalle": e.get("lectura", ""),
    } for e in ultimas]
    st.dataframe(pd.DataFrame(filas), use_container_width=True, hide_index=True)

    fallidas = [e for e in ultimas if e.get("estado") == "FALLO"]
    if fallidas:
        with st.expander(f"🔴 Ver por qué falló la última ({fallidas[0].get('inicio','')[:16]})"):
            paso = next((p for p in fallidas[0]["pasos"]
                         if p["estado"] == "FALLO"), None)
            if paso:
                st.markdown(f"**Falló en: {paso['titulo']}**")
                st.code(paso.get("salida", "")[-3000:] or "sin salida")


# --------------------------------------------------------------------------- #
# Tareas programadas
# --------------------------------------------------------------------------- #
def panel_tareas(cfg, ruta_config: str, usuario: str, puede_editar: bool) -> None:
    from ptnt.jobs import (PLANES, AlmacenProgramaciones, Bitacora,
                           Programacion, ProgramacionError, comando_windows,
                           instrucciones, linea_cron)

    st.subheader("Actualización automática")
    st.caption("Para que los números se refresquen solos, sin que nadie tenga "
               "que acordarse.")

    st.info("**Cómo funciona.** El programa no se queda corriendo en segundo "
            "plano. La cita la guarda el **Programador de tareas de Windows**, "
            "que ya sobrevive a los reinicios del servidor. Aquí se define qué "
            "hacer y se obtiene la línea exacta que hay que registrar allí — "
            "una sola vez, por el administrador.")

    alm = AlmacenProgramaciones(Path(cfg.rutas.salidas) / "tareas.json")
    bit = Bitacora(Path(cfg.rutas.salidas) / "ejecuciones")
    corridas = bit.ultimas(200)
    tareas = alm.listar()

    if tareas:
        filas = []
        for t in tareas:
            propias = [e for e in corridas if e.get("plan") == t.nombre]
            ultima = propias[0].get("inicio", "") if propias else ""
            ult_dt = None
            if ultima:
                try:
                    ult_dt = datetime.fromisoformat(ultima)
                except ValueError:
                    ult_dt = None
            if not t.activa:
                estado = "⏸️ pausada"
            elif t.vencida(ult_dt):
                estado = "🔴 se saltó su cita"
            else:
                estado = "🟢 al día"
            filas.append({
                "Tarea": t.nombre, "Cuándo": t.cuando(),
                "Última vez": ultima.replace("T", " ")[:16] or "nunca",
                "Próxima": f"{t.proxima():%d/%m %H:%M}", "Estado": estado})
        st.dataframe(pd.DataFrame(filas), use_container_width=True,
                     hide_index=True)

        saltadas = [f["Tarea"] for f in filas if "saltó" in f["Estado"]]
        if saltadas:
            # El planificador sabe si lanzó el proceso; no sabe si el proceso
            # hizo lo que debía. Esto es lo segundo, y es lo que importa.
            st.error(f"**{', '.join(saltadas)}** debería haberse ejecutado y no "
                     "hay rastro de ello. Compruebe que la tarea está registrada "
                     "en el Programador de Windows y que el servidor estuvo "
                     "encendido.")
    else:
        st.caption("No hay ninguna actualización automática definida.")

    if not puede_editar:
        st.caption("Solo un administrador puede crear o cambiar estas tareas.")
        return

    st.divider()
    with st.expander("➕  Crear una actualización automática"):
        c1, c2 = st.columns(2)
        with c1:
            nombre = st.text_input("Nombre", "actualizacion-diaria",
                                   help="Sin espacios. Así aparecerá en Windows.")
            plan = st.selectbox("Qué debe hacer", list(PLANES),
                                format_func=lambda c: PLANES[c][0])
            st.caption(PLANES[plan][2])
            csv = st.text_input("Archivo de consumos (si lo necesita)", "")
        with c2:
            frecuencia = st.selectbox("Cada cuánto",
                                      ["DIARIA", "SEMANAL", "MENSUAL"])
            hora = st.text_input("A qué hora", "03:00",
                                 help="De madrugada, para no competir con el "
                                      "trabajo de la gente.")
            dia_semana = "LUN"
            dia_mes = 1
            if frecuencia == "SEMANAL":
                dia_semana = st.selectbox(
                    "Qué día", ["LUN", "MAR", "MIE", "JUE", "VIE", "SAB", "DOM"])
            elif frecuencia == "MENSUAL":
                dia_mes = st.number_input("Qué día del mes", 1, 28, 1,
                                          help="Hasta el 28: hay meses sin 29, "
                                               "30 ni 31, y la tarea se saltaría "
                                               "en silencio.")

        if st.button("Guardar y ver cómo registrarla", type="primary"):
            try:
                tarea = Programacion(
                    nombre=nombre.strip(), plan=list(PLANES[plan][1]),
                    frecuencia=frecuencia, hora=hora,
                    dia_semana=dia_semana, dia_mes=int(dia_mes),
                    opciones={"analizar": {"csv": csv}} if csv else {},
                    descripcion=PLANES[plan][2], creada_por=usuario)
                alm.agregar(tarea, reemplazar=True)
            except ProgramacionError as exc:
                st.error(str(exc))
            else:
                st.success(f"Guardada: {tarea.cuando()}")
                st.markdown("##### Péguelo en el símbolo del sistema, "
                            "como administrador:")
                st.code(comando_windows(tarea, ruta_config,
                                        carpeta_trabajo=str(Path.cwd())),
                        language="bat")
                with st.expander("Instrucciones completas (y equivalente en Linux)"):
                    st.text(instrucciones(tarea, ruta_config,
                                          carpeta_trabajo=str(Path.cwd())))

    if tareas:
        with st.expander("⚙️  Pausar, reanudar o borrar"):
            elegida = st.selectbox("Tarea", [t.nombre for t in tareas])
            t = alm.obtener(elegida)
            c1, c2, c3 = st.columns(3)
            if c1.button("⏸️ Pausar" if t.activa else "▶️ Reanudar"):
                alm.activar(elegida, not t.activa)
                st.rerun()
            if c2.button("📋 Ver cómo registrarla"):
                st.code(comando_windows(t, ruta_config,
                                        carpeta_trabajo=str(Path.cwd())),
                        language="bat")
                st.code(linea_cron(t, ruta_config,
                                   carpeta_trabajo=str(Path.cwd())))
            if c3.button("🗑️ Borrar la definición"):
                alm.quitar(elegida)
                st.warning("Borrada aquí. **Si la registró en el Programador de "
                           "Windows, quítela también allí**, o seguirá "
                           f"ejecutándose:  `schtasks /Delete /TN "
                           f'"PTNT-BAL\\{elegida}" /F`')


# --------------------------------------------------------------------------- #
# Escenarios: probar cambios sin publicarlos
# --------------------------------------------------------------------------- #
def panel_escenarios(cfg, ruta_config: str, usuario: str, alcance) -> None:
    from ptnt.org.hierarchy import load_jerarquia
    from ptnt.security.scope import AlcanceError, exigir_entidad
    from ptnt.workspace import (AlmacenEscenarios, CambioPropuesto,
                                EscenarioError, comparar, energia_cabecera,
                                evaluar_escenario)

    st.subheader("Probar un cambio antes de aplicarlo")
    st.caption("Cambie lo que quiera sobre un alimentador y vea cómo saldría el "
               "balance. **Nada de esto se publica**: el modelo oficial no se "
               "toca hasta que usted lo decida.")

    ruta_cat = cfg.organizacion.catalogo or ""
    if not ruta_cat or not Path(ruta_cat).exists():
        st.warning("No hay catálogo organizacional cargado. Sin él no se puede "
                   "saber a qué unidad de negocio pertenece cada alimentador. "
                   "Indíquelo en `organizacion.catalogo` del archivo de "
                   "configuración.")
        return
    jer = load_jerarquia(ruta_cat)

    ruta_db = Path(cfg.rutas.salidas) / "escenarios" / "escenarios.db"
    with AlmacenEscenarios(ruta_db) as alm:
        escenarios = alm.listar(alcance=alcance, incluir_cerrados=False)

        st.markdown("#### Sus escenarios abiertos")
        if escenarios:
            st.dataframe(pd.DataFrame([{
                "Escenario": e.nombre, "Alcance": f"{e.nivel} {e.entidad}",
                "Unidad": e.unidad_negocio, "De": e.usuario, "Estado": e.estado,
            } for e in escenarios]), use_container_width=True, hide_index=True)
        else:
            st.caption("Ninguno todavía.")

        with st.expander("➕  Empezar uno nuevo"):
            c1, c2 = st.columns(2)
            nombre = c1.text_input("¿Qué está probando?",
                                   "Repotenciar un transformador")
            nivel = c2.selectbox("Sobre qué", ["ALIMENTADOR", "SUBESTACION"])
            if nivel == "ALIMENTADOR":
                opciones = jer.alimentadores_de() if alcance.matriz else [
                    a for a in jer.alimentadores_de()
                    if alcance.puede_ver(jer.unidad_de(a))]
            else:
                opciones = [s for s in jer.subestaciones
                            if alcance.matriz or any(
                                alcance.puede_ver(jer.unidad_de(a))
                                for a in jer.alimentadores_de(subestacion=s))]
            entidad = c1.selectbox("Cuál", opciones) if opciones else None
            comentario = c2.text_input("Nota (opcional)", "")

            if entidad and st.button("Crear el escenario", type="primary"):
                try:
                    res = exigir_entidad(alcance, jer, entidad, nivel)
                except AlcanceError as exc:
                    st.error(str(exc))
                else:
                    esc = alm.abrir(nombre=nombre, usuario=usuario,
                                    unidad_negocio=res.unidad_negocio,
                                    nivel=nivel, entidad=entidad,
                                    alimentadores=res.alimentadores,
                                    comentario=comentario)
                    st.success(f"Creado sobre {len(res.alimentadores)} "
                               "alimentador(es). Ya puede añadir cambios.")
                    st.rerun()

        if not escenarios:
            return

        st.divider()
        nombres = {f"{e.nombre} · {e.nivel} {e.entidad}": e for e in escenarios}
        elegido = nombres[st.selectbox("Escenario a trabajar", list(nombres))]
        cambios = alm.cambios(elegido.escenario_id)

        c1, c2 = st.columns([3, 2])
        with c1:
            st.markdown(f"#### Cambios acumulados ({len(cambios)})")
            if cambios:
                st.dataframe(pd.DataFrame([{
                    "Elemento": c.elemento_guid, "Campo": c.campo,
                    "Nuevo valor": c.valor_despues, "Motivo": c.motivo,
                } for c in cambios]), use_container_width=True, hide_index=True)
            else:
                st.caption("Ninguno todavía. Añada uno a la derecha.")

        with c2:
            st.markdown("#### Añadir un cambio")
            capa = st.selectbox("Qué tipo de elemento", [
                "ptnt_puesto_transformacion", "ptnt_cliente", "ptnt_luminaria",
                "ptnt_capacitor", "ptnt_seccionador"])
            elemento = st.text_input("Identificador del elemento", "")
            campo = st.text_input("Campo a cambiar", "kva")
            valor = st.text_input("Nuevo valor", "")
            motivo = st.text_input("¿Por qué?", "")
            if st.button("Añadir") and elemento and campo:
                try:
                    alm.acumular(elegido.escenario_id, [CambioPropuesto(
                        capa=capa, elemento_guid=elemento, campo=campo,
                        valor_despues=_num(valor), autor=usuario, motivo=motivo)])
                except EscenarioError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()

        st.divider()
        st.markdown("#### Calcular el balance con estos cambios")
        cab = st.text_input(
            "Medición de cabecera (CSV, opcional)", "",
            help="Con ella el resultado es verificable; sin ella, una estimación "
                 "que solo sirve para comparar una prueba con otra.")
        nota = st.text_input("¿Qué está probando en esta vuelta?", "")

        if st.button("🧮  Calcular ahora", type="primary"):
            medicion = None
            if cab:
                try:
                    medicion = energia_cabecera(cab)
                except (FileNotFoundError, ValueError) as exc:
                    st.error(str(exc))
                    medicion = None
            with st.spinner("Calculando… puede tardar unos minutos."):
                res = evaluar_escenario(elegido, cambios, cfg, cabecera=medicion)
            if not res.metricas:
                for a in res.advertencias:
                    st.error(a)
            else:
                alm.registrar_iteracion(
                    elegido.escenario_id, metricas=res.metricas,
                    n_cambios=len(cambios), hash_topologia=res.hash_topologia,
                    cambios_no_aplicados=[c.to_dict() for c in res.no_aplicados],
                    comentario=nota)
                _mostrar_evaluacion(res)

        evo = alm.evolucion(elegido.escenario_id)
        if not evo.empty:
            st.markdown("#### Cómo ha ido evolucionando")
            st.dataframe(evo, use_container_width=True, hide_index=True)
            if "pnt_pct" in evo.columns and len(evo) > 1:
                st.line_chart(evo.set_index("n")["pnt_pct"])
                try:
                    comp = comparar(alm, elegido.escenario_id,
                                    int(evo["n"].iloc[0]), int(evo["n"].iloc[-1]))
                    for a in comp.advertencias:
                        st.warning(a)
                except EscenarioError:
                    pass


def _mostrar_evaluacion(res) -> None:
    m = res.metricas
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Pérdida no técnica", f"{m.get('pnt_pct', 0):.2f} %")
    c2.metric("Pérdida técnica", f"{m.get('perdida_tecnica_kwh', 0):,.0f} kWh")
    c3.metric("Alimentadores", m.get("alimentadores", 0))
    c4.metric("Tipo de balance", m.get("tipo_balance", "—"))

    if res.verificable:
        st.success(res.lectura())
    else:
        # Dar la PNT sin decir que es estimada convierte una estimación en un
        # hecho, que es el error más caro de este tipo de proyecto.
        st.warning(res.lectura())

    for a in res.advertencias:
        st.warning(a)
    for c in res.controles:
        st.error(c)
    for c in res.no_aplicados[:10]:
        st.error(f"{c.capa} / {c.elemento_guid}: {c.motivo}")

    if not res.por_alimentador.empty and len(res.por_alimentador) > 1:
        st.dataframe(res.por_alimentador, use_container_width=True,
                     hide_index=True)


def _num(v: str):
    for conv in (int, float):
        try:
            return conv(v)
        except (TypeError, ValueError):
            continue
    return v
