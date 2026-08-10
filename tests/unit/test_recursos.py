"""Ajuste a los recursos del equipo: paralelismo, cola de espera y admisión.

Lo que se fija aquí es que la plataforma **no** intente hacer más de lo que el
equipo aguanta. Lanzar dieciséis alimentadores en un servidor de 16 GB no da
dieciséis veces la velocidad: da *swap*, y con swap el lote tarda más que en
secuencial — cuando no termina con procesos muertos y horas de cálculo perdidas
sin explicación.
"""

import threading
import time

import pytest

from ptnt.runtime.gate import Portero, ServicioSaturado
from ptnt.runtime.pool import EjecutorTareas
from ptnt.runtime.resources import (Recursos, calcular_presupuesto,
                                    memoria_disponible_mb)

_GB = 1 << 30


def _equipo(cpus: int, disponible_mb: int, total_mb: int | None = None) -> Recursos:
    return Recursos(cpus=cpus, ram_total_mb=total_mb or disponible_mb,
                    ram_disponible_mb=disponible_mb, fuente="prueba")


def _cgroup_v2(raiz, *, memoria=None, usado=0, cache=0, cpus=None,
               periodo=100_000):
    """Árbol de ficheros que imita el cgroup v2 de un contenedor."""

    raiz.mkdir(parents=True, exist_ok=True)
    (raiz / "memory.max").write_text("max" if memoria is None else str(memoria))
    (raiz / "memory.current").write_text(str(usado))
    (raiz / "memory.stat").write_text(f"anon 1024\ninactive_file {cache}\n")
    (raiz / "cpu.max").write_text(
        "max 100000" if cpus is None else f"{int(cpus * periodo)} {periodo}")
    return raiz


def _cgroup_v1(raiz, *, memoria=None, usado=0, cache=0, cuota=-1,
               periodo=100_000):
    """Lo mismo para cgroup v1, que reparte los mismos datos en otros ficheros."""

    raiz.mkdir(parents=True, exist_ok=True)
    (raiz / "memory").mkdir(exist_ok=True)
    (raiz / "cpu").mkdir(exist_ok=True)
    # v1 no escribe "max": escribe un entero astronómico cuando no hay tope.
    sin_tope = 9223372036854771712
    (raiz / "memory/memory.limit_in_bytes").write_text(
        str(sin_tope if memoria is None else memoria))
    (raiz / "memory/memory.usage_in_bytes").write_text(str(usado))
    (raiz / "memory/memory.stat").write_text(f"total_inactive_file {cache}\n")
    (raiz / "cpu/cpu.cfs_quota_us").write_text(str(cuota))
    (raiz / "cpu/cpu.cfs_period_us").write_text(str(periodo))
    return raiz


# --------------------------------------------------------------------------- #
# Límites del contenedor
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_dentro_de_un_contenedor_manda_el_limite_del_contenedor(tmp_path):
    """Con `--memory=2g`, /proc/meminfo sigue mostrando la RAM del anfitrión. Sin
    leer el cgroup, la plataforma creería tener 64 GB, lanzaría treinta
    alimentadores y el núcleo mataría el contenedor entero."""

    raiz = _cgroup_v2(tmp_path, memoria=2 * _GB, usado=512 * (1 << 20))
    r = Recursos.detectar(raiz_cgroup=raiz)

    assert r.ram_total_mb == 2048, "el tope del contenedor, no el del anfitrión"
    assert r.ram_disponible_mb <= 1536
    assert r.en_contenedor and r.contenedor == "cgroup v2"
    assert "limitado_por_contenedor" in r.resumen()


@pytest.mark.unit
def test_la_cache_reclamable_no_cuenta_como_memoria_ocupada(tmp_path):
    """Leer un GeoPackage grande llena la caché del cgroup. Si esa caché contara
    como ocupada, la plataforma pasaría a procesar de a un alimentador por creer
    llena una memoria que el núcleo devuelve en cuanto se la piden."""

    con_cache = memoria_disponible_mb(raiz_cgroup=_cgroup_v2(
        tmp_path / "a", memoria=4 * _GB, usado=3 * _GB, cache=2 * _GB))
    sin_cache = memoria_disponible_mb(raiz_cgroup=_cgroup_v2(
        tmp_path / "b", memoria=4 * _GB, usado=3 * _GB, cache=0))

    assert sin_cache == 1024                    # 4 GB - 3 GB
    assert con_cache > sin_cache                # la caché vuelve a estar libre
    assert con_cache <= 4096, "nunca más que el tope del contenedor"


@pytest.mark.unit
def test_la_cuota_de_cpu_acota_aunque_la_afinidad_no_lo_haga(tmp_path):
    """`--cpus=2` no toca la afinidad, toca la cuota CFS: `sched_getaffinity`
    devuelve los núcleos del anfitrión y se lanzarían procesos que el
    planificador se limita a estrangular."""

    r = Recursos.detectar(raiz_cgroup=_cgroup_v2(tmp_path, cpus=2))
    assert r.cpus <= 2


@pytest.mark.unit
def test_medio_nucleo_de_cuota_sigue_siendo_un_trabajador(tmp_path):
    """Redondear hacia abajo no puede dejar el lote sin procesar: con 0,5 núcleos
    se trabaja de a uno, despacio, pero se trabaja."""

    r = Recursos.detectar(raiz_cgroup=_cgroup_v2(tmp_path, cpus=0.5))
    assert r.cpus == 1


@pytest.mark.unit
def test_cgroup_v1_se_lee_igual_que_v2(tmp_path):
    """Los servidores de la empresa no están todos en la misma versión."""

    raiz = _cgroup_v1(tmp_path, memoria=3 * _GB, usado=_GB, cuota=200_000)
    r = Recursos.detectar(raiz_cgroup=raiz)

    assert r.ram_total_mb == 3072
    assert r.cpus <= 2
    assert r.contenedor == "cgroup v1"


@pytest.mark.unit
def test_sin_tope_en_el_cgroup_se_usa_el_equipo_entero(tmp_path):
    """Un cgroup sin límite —que es lo normal fuera de un contenedor— no puede
    hacer que la plataforma se crea encerrada y desperdicie el servidor."""

    for raiz in (_cgroup_v2(tmp_path / "v2"), _cgroup_v1(tmp_path / "v1")):
        r = Recursos.detectar(raiz_cgroup=raiz)
        assert not r.en_contenedor
        assert r.ram_total_mb > 0 and r.cpus >= 1


@pytest.mark.unit
def test_un_contenedor_mas_grande_que_el_anfitrion_no_infla_la_memoria(tmp_path):
    """Un cgroup puede declarar 1 TB en un equipo de 16 GB. El que manda ahí es
    el anfitrión: creerse el número del cgroup sería paginar hasta morir."""

    real = Recursos.detectar()
    r = Recursos.detectar(raiz_cgroup=_cgroup_v2(tmp_path, memoria=1024 * _GB))

    assert r.ram_total_mb == real.ram_total_mb
    assert not r.en_contenedor


# --------------------------------------------------------------------------- #
# Presupuesto
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_la_memoria_manda_sobre_los_nucleos():
    """16 núcleos y 8 GB no son 16 tareas de 2 GB: son 2, y el resto espera."""

    p = calcular_presupuesto(coste_mb_por_tarea=2048,
                             ram_reservada_mb=2048,
                             fraccion_ram_utilizable=1.0,
                             recursos=_equipo(cpus=16, disponible_mb=8192))
    assert p.trabajadores == 3          # (8192 - 2048) / 2048
    assert p.limitado_por == "memoria"
    assert "memoria" in p.explicacion()


@pytest.mark.unit
def test_con_memoria_de_sobra_manda_la_cpu():
    p = calcular_presupuesto(coste_mb_por_tarea=256,
                             ram_reservada_mb=2048,
                             recursos=_equipo(cpus=8, disponible_mb=64_000))
    assert p.trabajadores == 8
    assert p.limitado_por == "cpu"


@pytest.mark.unit
def test_siempre_cabe_al_menos_una():
    """Un equipo al límite tiene que poder procesar de a uno; lo contrario es
    quedarse sin hacer nada, que es peor que ir despacio."""

    p = calcular_presupuesto(coste_mb_por_tarea=8192,
                             ram_reservada_mb=2048,
                             recursos=_equipo(cpus=4, disponible_mb=3000))
    assert p.trabajadores == 1


@pytest.mark.unit
def test_la_reserva_del_sistema_no_se_toca():
    """Sin reserva, un recálculo pesado deja a las cuadrillas sin poder
    descargar su trabajo: la API comparte el equipo."""

    equipo = _equipo(cpus=8, disponible_mb=10_000)
    sin_reserva = calcular_presupuesto(coste_mb_por_tarea=1024,
                                       ram_reservada_mb=0,
                                       fraccion_ram_utilizable=1.0,
                                       recursos=equipo)
    con_reserva = calcular_presupuesto(coste_mb_por_tarea=1024,
                                       ram_reservada_mb=4096,
                                       fraccion_ram_utilizable=1.0,
                                       recursos=equipo)
    assert con_reserva.trabajadores < sin_reserva.trabajadores
    assert con_reserva.ram_utilizable_mb == 10_000 - 4096


@pytest.mark.unit
def test_el_tope_de_configuracion_gana():
    """Un servidor compartido con otro servicio se acota a mano, y eso manda."""

    p = calcular_presupuesto(coste_mb_por_tarea=128,
                             tope=2,
                             recursos=_equipo(cpus=32, disponible_mb=128_000))
    assert p.trabajadores == 2
    assert p.limitado_por == "configuracion"


@pytest.mark.unit
def test_la_espera_de_red_no_se_limita_por_nucleos():
    """Once bases de datos en un equipo de 4 núcleos: el tiempo se va esperando
    a la red, no calculando. Atarlo a los núcleos dejaría la ingesta a un tercio
    de su velocidad por un límite que no aplica."""

    p = calcular_presupuesto(coste_mb_por_tarea=64, cpus_maximos=11, tope=11,
                             ligado_a_cpu=False,
                             recursos=_equipo(cpus=4, disponible_mb=32_000))
    assert p.trabajadores == 11
    assert p.limitado_por == "concurrencia"


# --------------------------------------------------------------------------- #
# Ejecutor: cola, aislamiento de fallos, resultados
# --------------------------------------------------------------------------- #
def _duplicar(n: int) -> int:
    return n * 2


def _falla_en_siete(n: int) -> int:
    if n == 7:
        raise ValueError("alimentador con datos corruptos")
    return n


@pytest.mark.unit
def test_procesa_todo_aunque_haya_mas_tareas_que_trabajadores():
    """Lo que no cabe entra en cola, no se descarta."""

    p = calcular_presupuesto(coste_mb_por_tarea=1, tope=2,
                             recursos=_equipo(cpus=8, disponible_mb=64_000))
    ejecutor = EjecutorTareas(presupuesto=p, tipo="io", nombre="prueba")
    lote = ejecutor.ejecutar(_duplicar, [(f"t{i}", (i,)) for i in range(20)])

    assert len(lote.resultados) == 20
    assert not lote.fallidos
    assert lote.valores()["t5"] == 10
    assert lote.resumen()["trabajadores"] == 2


@pytest.mark.unit
def test_un_fallo_no_cancela_el_lote():
    """Un alimentador con datos corruptos no puede arruinar las otras 499 horas
    de cálculo."""

    p = calcular_presupuesto(coste_mb_por_tarea=1, tope=3,
                             recursos=_equipo(cpus=8, disponible_mb=64_000))
    lote = EjecutorTareas(presupuesto=p, tipo="io").ejecutar(
        _falla_en_siete, [(f"t{i}", (i,)) for i in range(12)])

    assert len(lote.ok) == 11
    assert len(lote.fallidos) == 1
    assert lote.fallidos[0].clave == "t7"
    assert "datos corruptos" in lote.fallidos[0].error
    assert "t7" in lote.informe()


@pytest.mark.unit
def test_sin_paralelismo_no_se_arranca_ningun_proceso():
    """Con un solo trabajador no se paga el coste de serializar argumentos ni de
    arrancar intérpretes: en un lote pequeño eso domina el tiempo."""

    p = calcular_presupuesto(coste_mb_por_tarea=99_999,
                             recursos=_equipo(cpus=1, disponible_mb=1000))
    assert p.trabajadores == 1
    lote = EjecutorTareas(presupuesto=p, tipo="cpu").ejecutar(
        _duplicar, [("a", (3,)), ("b", (4,))])
    assert lote.valores() == {"a": 6, "b": 8}


@pytest.mark.unit
def test_la_espera_en_cola_queda_medida():
    """Es la métrica que dice si el equipo se quedó corto, y la que justifica
    pedir más máquina."""

    p = calcular_presupuesto(coste_mb_por_tarea=1, tope=1,
                             recursos=_equipo(cpus=8, disponible_mb=64_000))
    ejecutor = EjecutorTareas(presupuesto=p, tipo="io")
    lote = ejecutor.ejecutar(time.sleep, [(f"t{i}", (0.05,)) for i in range(4)])

    esperas = [r.espera_s for r in lote.resultados]
    assert esperas[0] < esperas[-1], "las últimas esperan más que las primeras"
    assert esperas[-1] >= 0.1


@pytest.mark.unit
def test_las_tareas_se_consumen_de_forma_perezosa():
    """Con 5 000 alimentadores, materializar la lista entera antes de calcular
    nada ya es parte del problema."""

    materializadas = []

    def generador():
        for i in range(50):
            materializadas.append(i)
            yield (f"t{i}", (i,))

    p = calcular_presupuesto(coste_mb_por_tarea=1, tope=2,
                             recursos=_equipo(cpus=8, disponible_mb=64_000))
    ejecutor = EjecutorTareas(presupuesto=p, tipo="io")
    lote = ejecutor.ejecutar(_duplicar, generador())

    assert len(lote.resultados) == 50
    # Y el generador se consumió a demanda, no de golpe al empezar.
    assert materializadas == list(range(50))


# --------------------------------------------------------------------------- #
# Prioridad: lo urgente no espera detrás de lo rutinario
# --------------------------------------------------------------------------- #
def _identidad(n):
    return n


@pytest.mark.unit
def test_lo_urgente_se_adelanta_a_lo_rutinario():
    """En un recálculo de 400 alimentadores el analista espera por unos pocos.
    Que salgan al final por haber entrado al final de la lista es tiempo perdido
    de una persona."""

    p = calcular_presupuesto(coste_mb_por_tarea=1, tope=1,
                             recursos=_equipo(cpus=8, disponible_mb=64_000))
    ejecutor = EjecutorTareas(presupuesto=p, tipo="io")

    tareas = [(f"rutina-{i}", (i,), 0) for i in range(400)]
    tareas.append(("URGENTE", (99,), 10))       # la última en entrar

    lote = ejecutor.ejecutar(_identidad, tareas)
    salida = [r.clave for r in lote.resultados]

    assert len(salida) == 401
    # La primera ya está en vuelo cuando se elige la siguiente; de ahí en
    # adelante manda la prioridad, no el orden de llegada.
    assert salida.index("URGENTE") <= 1


@pytest.mark.unit
def test_mas_alla_de_la_ventana_la_prioridad_no_alcanza():
    """Se fija el límite real: con una fuente perezosa más larga que la ventana,
    lo urgente que aún no se ha visto no puede adelantar a nada. Es el precio de
    no agotar la fuente antes de empezar, y conviene que esté escrito."""

    p = calcular_presupuesto(coste_mb_por_tarea=1, tope=1,
                             recursos=_equipo(cpus=8, disponible_mb=64_000))
    ejecutor = EjecutorTareas(presupuesto=p, tipo="io", ventana_prioridad=4)

    tareas = [(f"rutina-{i}", (i,), 0) for i in range(30)]
    tareas.append(("URGENTE", (99,), 10))

    salida = [r.clave for r in ejecutor.ejecutar(_identidad, tareas).resultados]
    assert salida.index("URGENTE") > 4, "no se adelanta a lo que no se ha visto"
    assert salida[-1] != "URGENTE", "pero sí a todo lo que entra en la ventana"


@pytest.mark.unit
def test_sin_prioridades_el_orden_sigue_siendo_fifo():
    """Quien no usa prioridades no puede notar el cambio: dos tareas iguales
    tienen que salir en el orden en que entraron, no en uno arbitrario."""

    p = calcular_presupuesto(coste_mb_por_tarea=1, tope=1,
                             recursos=_equipo(cpus=8, disponible_mb=64_000))
    lote = EjecutorTareas(presupuesto=p, tipo="io").ejecutar(
        _identidad, [(f"t{i:02d}", (i,)) for i in range(15)])

    assert [r.clave for r in lote.resultados] == [f"t{i:02d}" for i in range(15)]


@pytest.mark.unit
def test_la_prioridad_no_materializa_la_fuente_entera():
    """Ordenar globalmente exigiría traerse los 5 000 alimentadores a memoria
    antes de calcular nada, que es justo lo que este módulo evita."""

    vistas = []
    adelanto = []

    def generador():
        for i in range(500):
            vistas.append(i)
            yield (f"t{i}", (i,), i % 3)

    p = calcular_presupuesto(coste_mb_por_tarea=1, tope=2,
                             recursos=_equipo(cpus=8, disponible_mb=64_000))
    ejecutor = EjecutorTareas(presupuesto=p, tipo="io", ventana_prioridad=16)

    def _anotar(r):
        # Cuánto se había leído de la fuente por delante de lo ya terminado.
        adelanto.append(len(vistas) - len(adelanto))

    lote = ejecutor.ejecutar(_identidad, generador(), al_terminar=_anotar)

    assert len(lote.resultados) == 500, "y aun así se procesan todas"
    # La ventana (16) más las que estén en vuelo (2): nunca los 500 de golpe.
    assert max(adelanto) <= 16 + 2 + 1, f"adelanto máximo {max(adelanto)}"


# --------------------------------------------------------------------------- #
# Reintento: lo pasajero se reintenta, lo corrupto no
# --------------------------------------------------------------------------- #
_INTENTOS: dict = {}


def _falla_una_vez_por_red(clave: str):
    """Se cae la primera vez con un error de red, luego responde."""

    _INTENTOS[clave] = _INTENTOS.get(clave, 0) + 1
    if _INTENTOS[clave] == 1:
        raise ConnectionResetError("conexión reiniciada por la base")
    return f"{clave}:ok"


def _datos_corruptos(clave: str):
    raise ValueError("columna CLIRLSCOD ausente en el padrón")


@pytest.mark.unit
def test_un_corte_de_red_no_pierde_la_unidad_de_negocio():
    """Un corte de dos segundos en una de once bases no puede costar la ingesta
    entera de esa unidad de negocio."""

    _INTENTOS.clear()
    p = calcular_presupuesto(coste_mb_por_tarea=1, tope=2,
                             recursos=_equipo(cpus=8, disponible_mb=64_000))
    ejecutor = EjecutorTareas(presupuesto=p, tipo="io", reintentos=2,
                              espera_reintento_s=0.01)
    lote = ejecutor.ejecutar(_falla_una_vez_por_red,
                             [(f"UN-{i:02d}", (f"UN-{i:02d}",)) for i in range(4)])

    assert len(lote.ok) == 4, "las cuatro acaban leídas"
    assert not lote.fallidos
    assert lote.reintentos == 4
    assert len(lote.recuperados) == 4, "y consta que no salieron a la primera"
    assert all(r.intentos == 2 for r in lote.recuperados)


@pytest.mark.unit
def test_los_datos_corruptos_no_se_reintentan():
    """Repetir un alimentador con datos malos gasta el lote dos veces para
    fallar igual."""

    p = calcular_presupuesto(coste_mb_por_tarea=1, tope=2,
                             recursos=_equipo(cpus=8, disponible_mb=64_000))
    ejecutor = EjecutorTareas(presupuesto=p, tipo="io", reintentos=3,
                              espera_reintento_s=0.01)
    lote = ejecutor.ejecutar(_datos_corruptos, [("ALI-001", ("ALI-001",))])

    assert len(lote.fallidos) == 1
    assert lote.fallidos[0].intentos == 1, "un solo intento, no cuatro"
    assert lote.reintentos == 0
    assert not lote.fallidos[0].transitorio


@pytest.mark.unit
def test_el_reintento_se_agota_y_el_fallo_se_reporta():
    """Una base caída de verdad no puede dejar el lote reintentando para
    siempre: se agota, se reporta y se sigue con las demás."""

    def _siempre_cae(_clave):
        raise TimeoutError("la base no responde")

    p = calcular_presupuesto(coste_mb_por_tarea=1, tope=2,
                             recursos=_equipo(cpus=8, disponible_mb=64_000))
    ejecutor = EjecutorTareas(presupuesto=p, tipo="io", reintentos=2,
                              espera_reintento_s=0.01)
    lote = ejecutor.ejecutar(_siempre_cae, [("UN-07", ("UN-07",)),
                                            ("UN-08", ("UN-08",))])

    assert len(lote.fallidos) == 2
    assert all(r.intentos == 3 for r in lote.fallidos), "1 intento + 2 reintentos"
    assert "no responde" in lote.informe()


@pytest.mark.unit
def test_un_fichero_que_no_esta_no_se_reintenta():
    """La geodatabase ausente seguirá ausente dentro de dos segundos. Es un
    OSError, pero no de los que se arreglan solos."""

    from ptnt.runtime.pool import es_transitorio

    assert not es_transitorio(FileNotFoundError("falta la .gdb"))
    assert not es_transitorio(PermissionError("sin permiso"))
    assert not es_transitorio(ValueError("dato malo"))
    assert es_transitorio(ConnectionResetError("cortó la base"))
    assert es_transitorio(TimeoutError("sin respuesta"))


@pytest.mark.unit
def test_el_error_del_conector_se_clasifica_por_nombre_de_clase():
    """No se puede importar cx_Oracle para clasificar su error: el conector que
    falla vive en el proceso hijo y puede no estar instalado aquí."""

    from ptnt.runtime.pool import es_transitorio

    class OperationalError(Exception):
        """Como la de cx_Oracle/psycopg: sesión caída, base ocupada."""

    class DatabaseError(Exception):
        """En Oracle cubre también «la tabla no existe»: reintentar no arregla."""

    assert es_transitorio(OperationalError("ORA-03113: fin de archivo"))
    assert not es_transitorio(DatabaseError("ORA-00942: la tabla no existe"))


# --------------------------------------------------------------------------- #
# Control de admisión de la API
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_solo_pasan_los_que_caben_y_el_resto_espera():
    """Cuarenta cuadrillas volviendo a las cinco de la tarde no pueden abrir
    cuarenta GeoPackages a la vez."""

    portero = Portero("subidas", limite=2, max_en_cola=10, espera_maxima_s=5)
    pico = {"valor": 0}
    lock = threading.Lock()
    activos = {"n": 0}

    def trabajo():
        with portero.turno():
            with lock:
                activos["n"] += 1
                pico["valor"] = max(pico["valor"], activos["n"])
            time.sleep(0.05)
            with lock:
                activos["n"] -= 1

    hilos = [threading.Thread(target=trabajo) for _ in range(8)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert pico["valor"] <= 2, "nunca más de dos a la vez"
    assert portero.resumen()["atendidas"] == 8, "y las ocho se atendieron"


@pytest.mark.unit
def test_la_cola_llena_responde_reintente_en_vez_de_aceptar():
    """Una cola infinita no es amabilidad: es un timeout en el teléfono del
    técnico y un servidor acumulando peticiones que ya nadie espera."""

    portero = Portero("subidas", limite=1, max_en_cola=0, espera_maxima_s=5)
    liberar = threading.Event()

    def ocupar():
        with portero.turno():
            liberar.wait(timeout=2)

    h = threading.Thread(target=ocupar)
    h.start()
    time.sleep(0.05)

    with pytest.raises(ServicioSaturado) as exc:
        with portero.turno():
            pass
    assert exc.value.reintentar_en_s > 0
    assert "Reintente" in str(exc.value)

    liberar.set()
    h.join()
    assert portero.resumen()["rechazadas"] == 1


@pytest.mark.unit
def test_el_turno_se_libera_aunque_el_bloque_falle():
    """Un error procesando un paquete no puede consumir una plaza para siempre y
    estrangular el servicio hasta el siguiente reinicio."""

    portero = Portero("subidas", limite=1, max_en_cola=1)
    with pytest.raises(RuntimeError):
        with portero.turno():
            raise RuntimeError("paquete corrupto")

    assert portero.en_curso == 0
    with portero.turno():
        assert portero.en_curso == 1


@pytest.mark.unit
def test_esperar_demasiado_tambien_se_rechaza():
    """Mejor decir «vuelva en 30 segundos» que dejar al técnico mirando una
    barra de progreso diez minutos."""

    portero = Portero("descargas", limite=1, max_en_cola=5,
                      espera_maxima_s=0.15)
    liberar = threading.Event()

    def ocupar():
        with portero.turno():
            liberar.wait(timeout=2)

    h = threading.Thread(target=ocupar)
    h.start()
    time.sleep(0.05)

    t0 = time.monotonic()
    with pytest.raises(ServicioSaturado):
        with portero.turno():
            pass
    assert time.monotonic() - t0 < 1.0, "no se queda esperando indefinidamente"

    liberar.set()
    h.join()


@pytest.mark.unit
def test_el_reintento_sugerido_escala_con_la_cola():
    """Si cuarenta rechazados vuelven en el mismo segundo, la saturación se
    repite en bucle en vez de resolverse."""

    portero = Portero("subidas", limite=1, max_en_cola=0, espera_maxima_s=20)
    corto = portero._reintento_sugerido()
    portero._en_cola = 15
    largo = portero._reintento_sugerido()
    assert largo > corto
