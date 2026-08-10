"""Cómo se ajusta la plataforma a los recursos del equipo.

Demuestra las tres cosas que en la operación llegan de a muchas y que sin límite
tumban el servidor:

    1. Muchos alimentadores  → se procesan N a la vez y el resto espera en cola
    2. Muchas bases de datos → se leen en paralelo, con tope por base
    3. Muchos dispositivos   → descargas y subidas acotadas, con 503 + Retry-After

Y las tres decisiones que gobiernan la cola:

    · el límite es el del contenedor, no el del anfitrión
    · lo urgente se adelanta a lo rutinario
    · lo pasajero se reintenta, lo corrupto no

Uso:  python scripts/demo_recursos.py
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from ptnt.runtime.gate import Portero, ServicioSaturado          # noqa: E402
from ptnt.runtime.pool import EjecutorTareas                     # noqa: E402
from ptnt.runtime.resources import Recursos, calcular_presupuesto  # noqa: E402


def titulo(n: int, texto: str) -> None:
    print(f"\n{'=' * 78}\n  {n}. {texto}\n{'=' * 78}")


def _trabajo_cpu(semilla: int) -> int:
    """Carga sintética que simula el flujo de potencia de un alimentador."""

    total = 0
    for i in range(400_000):
        total = (total + i * semilla) % 1_000_003
    return total


def _trabajo_io(segundos: float) -> str:
    """Lectura de una base: casi todo el tiempo es esperar a la red."""

    time.sleep(segundos)
    return "ok"


_FALLOS: dict[str, int] = {}


def _lectura_inestable(clave: str) -> str:
    """Una red que se cae a ratos, y una base con el padrón mal formado."""

    _FALLOS[clave] = _FALLOS.get(clave, 0) + 1
    if clave == "UN-04":
        # Esto no lo arregla ningún reintento: es el dato, no el enlace.
        raise ValueError("columna CLIRLSCOD ausente en el padrón")
    if _FALLOS[clave] <= 2 and clave in ("UN-02", "UN-07"):
        raise ConnectionResetError("la base cortó la sesión")
    return f"{clave}: leída"


def main() -> int:
    r = Recursos.detectar()

    titulo(1, "QUÉ HAY EN ESTE EQUIPO")
    for k, v in r.resumen().items():
        print(f"   {k:.<34} {v}")
    if r.en_contenedor:
        print(f"\n   Se está dentro de un contenedor ({r.contenedor}): manda su")
        print("   límite, no lo que tenga el anfitrión.")
    else:
        print("\n   Equipo desnudo (o contenedor sin tope de memoria ni cuota de")
        print("   CPU). Dentro de uno con `--memory=2g`, /proc/meminfo seguiría")
        print("   mostrando la RAM del anfitrión: por eso se lee el cgroup y se")
        print("   toma el menor de los dos. Creerse los 64 GB del anfitrión en un")
        print("   contenedor de 2 GB es que el núcleo lo mate a media tarde.")

    titulo(2, "CUÁNTO CABE, SEGÚN LO QUE CUESTE CADA ALIMENTADOR")
    print(f"   {'Coste/alimentador':>18} {'Trabajadores':>13} {'Limita':>14}")
    for coste in (128, 512, 2048, 8192):
        p = calcular_presupuesto(coste_mb_por_tarea=coste, recursos=r)
        print(f"   {coste:>15,} MB {p.trabajadores:>13} {p.limitado_por:>14}")
    print("\n   La memoria manda en cuanto el alimentador es grande. Lanzar uno")
    print("   por núcleo en un equipo justo no da más velocidad: da swap, y con")
    print("   swap el lote tarda MÁS que en secuencial.")

    titulo(3, "UN LOTE GRANDE: 24 ALIMENTADORES, CON COLA DE ESPERA")
    tareas = [(f"ALI-{i:03d}", (i + 1,)) for i in range(24)]

    for tope in (1, max(2, r.cpus // 2), r.cpus):
        p = calcular_presupuesto(coste_mb_por_tarea=64, tope=tope, recursos=r)
        ejecutor = EjecutorTareas(presupuesto=p, tipo="cpu",
                                  nombre=f"lote-{tope}")
        t0 = time.monotonic()
        lote = ejecutor.ejecutar(_trabajo_cpu, tareas)
        transcurrido = time.monotonic() - t0
        espera_max = max(x.espera_s for x in lote.resultados)
        print(f"   {tope:>2} trabajador(es): {transcurrido:6.2f} s  ·  "
              f"{len(lote.ok)}/{len(lote.resultados)} ok  ·  "
              f"espera máxima en cola {espera_max:5.2f} s")

    print("\n   Las 24 se procesan siempre. Lo que cambia es cuántas a la vez y")
    print("   cuánto espera la última: esa espera es el argumento con número")
    print("   para pedir más máquina.")

    titulo(4, "ONCE UNIDADES DE NEGOCIO: LECTURA EN PARALELO")
    lecturas = [(f"UN-{i:02d}", (0.30,)) for i in range(11)]

    for tope in (1, 6, 11):
        p = calcular_presupuesto(coste_mb_por_tarea=8, cpus_maximos=tope,
                                 tope=tope, ligado_a_cpu=False, recursos=r)
        ejecutor = EjecutorTareas(presupuesto=p, tipo="io", nombre="lecturas")
        t0 = time.monotonic()
        lote = ejecutor.ejecutar(_trabajo_io, lecturas)
        print(f"   {tope:>2} lectura(s) simultánea(s): {time.monotonic() - t0:5.2f} s "
              f"({len(lote.ok)}/11)")

    print("\n   En serie son once veces la latencia sumada; en paralelo, el tiempo")
    print("   de la base más lenta. Y NO se limita por núcleos: el tiempo se va")
    print("   esperando a la red, no calculando.")

    titulo(5, "MUCHOS DISPOSITIVOS PIDIENDO A LA VEZ")
    portero = Portero("subidas", limite=2, max_en_cola=4, espera_maxima_s=1.0)
    resultados: dict[str, str] = {}
    pico = {"n": 0, "max": 0}
    lock = threading.Lock()
    barrera = threading.Barrier(12)

    def cuadrilla(nombre: str) -> None:
        barrera.wait()
        try:
            with portero.turno(nombre):
                with lock:
                    pico["n"] += 1
                    pico["max"] = max(pico["max"], pico["n"])
                time.sleep(0.25)
                with lock:
                    pico["n"] -= 1
            resultados[nombre] = "atendida"
        except ServicioSaturado as exc:
            resultados[nombre] = f"reintente en {exc.reintentar_en_s} s"

    hilos = [threading.Thread(target=cuadrilla, args=(f"cuadrilla-{i:02d}",))
             for i in range(12)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    atendidas = sum(1 for v in resultados.values() if v == "atendida")
    rechazadas = len(resultados) - atendidas
    print(f"   12 cuadrillas sincronizando a la vez, límite de 2 + cola de 4:")
    print(f"     atendidas .......... {atendidas}")
    print(f"     con reintento ...... {rechazadas}")
    print(f"     pico simultáneo .... {pico['max']}  (nunca más de 2)")
    print(f"\n   Métricas del portero: {portero.resumen()}")
    print("\n   Las rechazadas NO pierden el trabajo: el paquete se guarda en el")
    print("   servidor antes de pedir turno, y la app reintenta sola con el")
    print("   Retry-After. Una cola infinita, en cambio, sería un timeout en el")
    print("   teléfono y un servidor acumulando peticiones que ya nadie espera.")

    titulo(6, "LO URGENTE NO ESPERA DETRÁS DE LO RUTINARIO")
    p = calcular_presupuesto(coste_mb_por_tarea=8, tope=1, recursos=r)
    rutina = [(f"rutina-{i:03d}", (0.001,), 0) for i in range(200)]
    # El alimentador con la cuadrilla en campo entra el último de la lista.
    rutina.append(("ALI-CAMPAÑA-EN-CURSO", (0.001,), 10))

    lote = EjecutorTareas(presupuesto=p, tipo="io",
                          nombre="prioridad").ejecutar(_trabajo_io, rutina)
    orden = [x.clave for x in lote.resultados]
    print(f"   201 tareas, la urgente entró la ÚLTIMA de la lista.")
    print(f"   Salió en la posición ......... {orden.index('ALI-CAMPAÑA-EN-CURSO') + 1}")
    print(f"   Sin prioridad habría salido en  201")
    print("\n   No es un detalle de eficiencia: el analista está esperando por")
    print("   ese alimentador, no por los otros doscientos. Los resultados se")
    print("   entregan según terminan, así que el orden es su tiempo.")

    titulo(7, "LO PASAJERO SE REINTENTA; LO CORRUPTO, NO")
    p = calcular_presupuesto(coste_mb_por_tarea=8, cpus_maximos=4, tope=4,
                             ligado_a_cpu=False, recursos=r)
    ejecutor = EjecutorTareas(presupuesto=p, tipo="io", nombre="ingesta",
                              reintentos=2, espera_reintento_s=0.05)
    lote = ejecutor.ejecutar(_lectura_inestable,
                             [(f"UN-{i:02d}", (f"UN-{i:02d}",)) for i in range(11)])

    print(f"   11 unidades de negocio, con una red que falla a ratos:")
    print(f"     leídas ............. {len(lote.ok)}/11")
    print(f"     reintentos ......... {lote.reintentos}")
    print(f"     recuperadas ........ {len(lote.recuperados)} "
          f"(salieron bien, pero no a la primera)")
    for f in lote.fallidos:
        print(f"     NO recuperable ..... {f.clave}: {f.error}")
    print("\n   La que falla por datos corruptos no se reintenta: repetirla")
    print("   gastaría el lote dos veces para fallar igual. Y las recuperadas")
    print("   se reportan aunque el lote acabe en verde: son la primera pista")
    print("   de que un enlace está por caerse del todo.")

    print(f"\n{'=' * 78}")
    print("  La plataforma se ajusta al equipo: procesa lo que cabe y encola el resto.")
    print(f"{'=' * 78}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
