"""Ejecución del proceso y tareas programadas.

Lo que se fija aquí: un fallo detiene la cadena en vez de calcular sobre datos a
medias, la bitácora se escribe mientras corre, y una tarea programada dice
cuándo toca sin mentir sobre si se saltó.
"""

from datetime import datetime, timedelta

import pytest

from ptnt.jobs import (AlmacenProgramaciones, Bitacora, Programacion,
                       ProgramacionError, comando_windows, ejecutar_plan,
                       linea_cron, resolver_plan, supuestos_previos)
from ptnt.jobs.pasos import PLANES, POR_CLAVE


# --------------------------------------------------------------------------- #
# Pasos y planes
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_los_pasos_se_ordenan_solos():
    """El usuario marca casillas; el orden no es cosa suya."""

    pasos = resolver_plan(["consolidar", "migrar", "analizar_red"])
    assert [p.clave for p in pasos] == ["migrar", "analizar_red", "consolidar"]


@pytest.mark.unit
def test_un_paso_inexistente_se_dice_con_los_que_hay():
    with pytest.raises(ValueError, match="no existen"):
        resolver_plan(["migrar", "inventado"])


@pytest.mark.unit
def test_lo_que_el_plan_da_por_hecho_es_aviso_y_no_error():
    """Preparar campo el martes se apoya en el balance del lunes. Obligar a
    recalcularlo todo convertiría un plan de cinco minutos en uno de cuarenta."""

    avisos = supuestos_previos(["diagnostico", "focalizar"])

    assert avisos, "debería avisar de lo que da por hecho"
    assert any("Calcular el balance" in a for a in avisos)
    # Pero no impide ejecutarlo.
    pasos = resolver_plan(["diagnostico", "focalizar"])
    assert [p.clave for p in pasos] == ["diagnostico", "focalizar"]


@pytest.mark.unit
def test_todos_los_planes_preparados_son_ejecutables():
    """Un plan de la lista que no resuelve sería un botón roto en el tablero."""

    for clave, (_titulo, plan, _expl) in PLANES.items():
        pasos = resolver_plan(list(plan))
        assert pasos, f"el plan '{clave}' quedó vacío"
        assert all(p.clave in POR_CLAVE for p in pasos)


@pytest.mark.unit
def test_una_bandera_booleana_se_pone_o_no_se_pone():
    """Pasarle 'False' como texto haría que typer la leyera como activada."""

    paso = POR_CLAVE["analizar_red"]
    assert "--trifasico" in paso.argumentos({"trifasico": True})
    assert "--trifasico" not in paso.argumentos({"trifasico": False})


# --------------------------------------------------------------------------- #
# Ejecución
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_un_fallo_detiene_la_cadena(tmp_path, monkeypatch):
    """Seguir calcularía el balance sobre una red que no se llegó a traer, y el
    número saldría con toda naturalidad."""

    from ptnt.jobs import ejecutor as ej

    class _Proc:
        def __init__(self, code):
            self.returncode = code
            self.stdout = "salida de prueba"
            self.stderr = ""

    # El segundo paso (migrar) falla.
    llamadas = []

    def falso_run(comando, **kw):
        llamadas.append(comando)
        return _Proc(2 if "migrar" in comando else 0)

    monkeypatch.setattr(ej.subprocess, "run", falso_run)

    bit = Bitacora(tmp_path / "bit")
    r = ejecutar_plan(["verificar", "migrar", "analizar_red", "consolidar"],
                      "config/base.yaml", bitacora=bit)

    estados = {p.clave: p.estado for p in r.pasos}
    assert estados["verificar"] == "CORRECTO"
    assert estados["migrar"] == "FALLO"
    assert estados["analizar_red"] == "OMITIDO"
    assert estados["consolidar"] == "OMITIDO"
    assert not r.correcto
    # Y no se llegó a invocar lo que venía después.
    assert not any("analizar-red" in c for c in llamadas)
    assert "no se ejecutaron" in r.lectura()


@pytest.mark.unit
def test_la_bitacora_se_escribe_mientras_corre(tmp_path, monkeypatch):
    """Si el servidor se reinicia a la mitad, tiene que quedar constancia de
    hasta dónde se llegó. Una bitácora que solo se escribe al final es la que
    falta justo el día que hace falta."""

    from ptnt.jobs import ejecutor as ej

    bit = Bitacora(tmp_path / "bit")
    vistos = []

    class _Proc:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def falso_run(comando, **kw):
        # A mitad de la corrida ya tiene que haber algo escrito en disco.
        vistos.append(bit.en_curso() is not None)
        return _Proc()

    monkeypatch.setattr(ej.subprocess, "run", falso_run)
    r = ejecutar_plan(["verificar", "fuentes"], "config/base.yaml", bitacora=bit)

    assert all(vistos), "la bitácora no estaba en disco durante la ejecución"
    guardado = bit.leer(r.ejecucion_id)
    assert guardado["estado"] == "CORRECTO"
    assert len(guardado["pasos"]) == 2
    assert bit.en_curso() is None, "al terminar ya no debe figurar en curso"


@pytest.mark.unit
def test_el_resumen_deja_fuera_las_trazas_de_registro(tmp_path, monkeypatch):
    """Quien lee el tablero quiere saber cómo acabó el paso, no el diario
    técnico de lo que hizo por dentro."""

    from ptnt.jobs import ejecutor as ej

    class _Proc:
        returncode = 0
        stdout = ("2026-08-22 14:00:54.312 | INFO | ptnt.pipeline:run:96 - algo\n"
                  "│ borde de tabla │\n"
                  "✓ Balance calculado: PNT 6,2 %\n")
        stderr = ""

    monkeypatch.setattr(ej.subprocess, "run", lambda c, **kw: _Proc())
    r = ejecutar_plan(["verificar"], "config/base.yaml")

    resumen = r.pasos[0].resumen
    assert "PNT 6,2 %" in resumen
    assert "INFO" not in resumen and "borde" not in resumen
    # La salida completa sí se conserva.
    assert "INFO" in r.pasos[0].salida


@pytest.mark.unit
def test_un_paso_que_no_se_puede_lanzar_no_tumba_la_corrida(tmp_path, monkeypatch):
    from ptnt.jobs import ejecutor as ej

    def revienta(comando, **kw):
        raise OSError("no such file")

    monkeypatch.setattr(ej.subprocess, "run", revienta)
    r = ejecutar_plan(["verificar"], "config/base.yaml")

    assert r.pasos[0].estado == "FALLO"
    assert "No se pudo lanzar" in r.pasos[0].salida


# --------------------------------------------------------------------------- #
# Programación
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_una_tarea_sin_pasos_no_se_acepta():
    with pytest.raises(ProgramacionError, match="no hace nada"):
        Programacion(nombre="vacia", plan=[])


@pytest.mark.unit
def test_el_dia_29_al_31_se_rechaza():
    """Del 29 al 31 hay meses que no lo tienen: la tarea se saltaría en silencio
    justo en febrero, que es cierre de mes."""

    with pytest.raises(ProgramacionError, match="1 y 28"):
        Programacion(nombre="m", plan=["migrar"], frecuencia="MENSUAL", dia_mes=31)


@pytest.mark.unit
def test_la_hora_mal_escrita_se_dice_con_el_formato():
    with pytest.raises(ProgramacionError, match="HH:MM"):
        Programacion(nombre="h", plan=["migrar"], hora="3am")


@pytest.mark.unit
def test_cuando_toca_la_proxima_vez():
    t = Programacion(nombre="d", plan=["migrar"], frecuencia="DIARIA", hora="03:00")

    # A las 10 de la mañana, la próxima es mañana a las 3.
    prox = t.proxima(datetime(2026, 8, 22, 10, 0))
    assert (prox.day, prox.hour) == (23, 3)
    # A la una de la madrugada, es hoy mismo.
    prox = t.proxima(datetime(2026, 8, 22, 1, 0))
    assert (prox.day, prox.hour) == (22, 3)


@pytest.mark.unit
def test_una_tarea_recien_creada_no_se_saltó_nada():
    """Sin esto, toda tarea nueva nace marcada en rojo y la señal deja de
    significar nada."""

    ahora = datetime(2026, 8, 22, 14, 30)
    t = Programacion(nombre="d", plan=["migrar"], hora="03:00",
                     creada=ahora.isoformat(timespec="seconds"))

    assert not t.vencida(None, ahora=ahora)


@pytest.mark.unit
def test_una_tarea_que_de_verdad_se_saltó_se_reporta():
    """El planificador sabe si lanzó el proceso; no sabe si el proceso hizo lo
    que debía. Esto es lo segundo."""

    creada = datetime(2026, 8, 1, 9, 0)
    t = Programacion(nombre="d", plan=["migrar"], hora="03:00",
                     creada=creada.isoformat(timespec="seconds"))
    ahora = datetime(2026, 8, 22, 14, 30)

    assert t.vencida(None, ahora=ahora), "nunca corrió desde que se creó"
    assert t.vencida(datetime(2026, 8, 20, 3, 5), ahora=ahora), "corrió anteayer"
    assert not t.vencida(datetime(2026, 8, 22, 3, 4), ahora=ahora), "corrió hoy"


@pytest.mark.unit
def test_las_ordenes_generadas_llevan_rutas_absolutas(tmp_path):
    """El planificador no arranca desde la carpeta del proyecto: una ruta
    relativa haría que no encontrara nada a las tres de la mañana."""

    t = Programacion(nombre="diaria", plan=["migrar"], hora="03:00")
    cfg = tmp_path / "config.yaml"
    cfg.write_text("x", encoding="utf-8")

    win = comando_windows(t, str(cfg), carpeta_trabajo=str(tmp_path))
    assert str(cfg.resolve()) in win
    assert "cd /d" in win, "sin fijar la carpeta, los resultados irían a System32"
    assert "/SC DAILY" in win and "/ST 03:00" in win

    cron = linea_cron(t, str(cfg), carpeta_trabajo=str(tmp_path))
    assert cron.startswith("0 3 * * *")
    assert str(cfg.resolve()) in cron


@pytest.mark.unit
def test_el_dia_de_la_semana_llega_bien_a_cada_planificador():
    t = Programacion(nombre="s", plan=["migrar"], frecuencia="SEMANAL",
                     dia_semana="MIE", hora="22:30")

    assert "/D WED" in comando_windows(t, "c.yaml")
    assert linea_cron(t, "c.yaml").startswith("30 22 * * 3")


@pytest.mark.unit
def test_las_tareas_sobreviven_al_reinicio(tmp_path):
    ruta = tmp_path / "tareas.json"
    alm = AlmacenProgramaciones(ruta)
    alm.agregar(Programacion(nombre="diaria", plan=["migrar", "analizar_red"]))

    otra = AlmacenProgramaciones(ruta)
    t = otra.obtener("diaria")
    assert t is not None and t.plan == ["migrar", "analizar_red"]

    with pytest.raises(ProgramacionError, match="Ya existe"):
        otra.agregar(Programacion(nombre="diaria", plan=["migrar"]))
    otra.agregar(Programacion(nombre="diaria", plan=["migrar"]), reemplazar=True)
    assert AlmacenProgramaciones(ruta).obtener("diaria").plan == ["migrar"]


@pytest.mark.unit
def test_una_tarea_corrupta_no_impide_leer_las_demas(tmp_path):
    import json

    ruta = tmp_path / "tareas.json"
    ruta.write_text(json.dumps({"tareas": [
        {"nombre": "rota", "plan": [], "frecuencia": "DIARIA"},
        {"nombre": "buena", "plan": ["migrar"], "frecuencia": "DIARIA",
         "hora": "03:00"},
    ]}), encoding="utf-8")

    nombres = [t.nombre for t in AlmacenProgramaciones(ruta).listar()]
    assert nombres == ["buena"]
