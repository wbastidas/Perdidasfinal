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
from ptnt.runtime.resources import Recursos, calcular_presupuesto


def _equipo(cpus: int, disponible_mb: int, total_mb: int | None = None) -> Recursos:
    return Recursos(cpus=cpus, ram_total_mb=total_mb or disponible_mb,
                    ram_disponible_mb=disponible_mb, fuente="prueba")


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
