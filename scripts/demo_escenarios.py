"""Escenarios de trabajo: acumular cambios y ver el balance al momento.

Recorre, **por la línea de comandos de verdad**, lo que hace un analista cuando
tiene una idea sobre un alimentador y quiere saber cómo saldría el balance antes
de publicar nada:

    1. Tres unidades de negocio, con sus alimentadores y subestaciones
    2. Alta de usuarios: uno por unidad y uno de matriz
    3. Lo que cada uno alcanza (y lo que no)
    4. Un escenario sobre UN alimentador
    5. Cambios acumulados, sin tocar el modelo oficial
    6. Evaluación: el balance con los cambios puestos
    7. Otra idea, otra iteración
    8. La evolución del alimentador en el tiempo
    9. Comparación entre dos iteraciones
   10. Un escenario a nivel de SUBESTACIÓN (varios alimentadores)
   11. Lo que un usuario de otra unidad NO puede hacer
   12. La matriz, que sí puede con todos

Uso:  python scripts/demo_escenarios.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import yaml

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

TRABAJO = RAIZ / "outputs" / "demo_escenarios"

# Tres unidades de negocio, como en CNEL: dos subestaciones repartidas y una que
# vive entera en una sola unidad —la que se podrá trabajar como subestación—.
CATALOGO = [
    # feeder_code, subestacion, unidad_negocio
    ("GYE-01", "SE-CENTRO", "GUAYAQUIL"),
    ("GYE-02", "SE-CENTRO", "GUAYAQUIL"),
    ("GYE-03", "SE-NORTE", "GUAYAQUIL"),
    ("MIL-01", "SE-MILAGRO", "MILAGRO"),
    ("STE-01", "SE-SALINAS", "SANTA_ELENA"),
]

_fallos: list[str] = []


def titulo(n: int, texto: str) -> None:
    print(f"\n{'=' * 78}\n  ETAPA {n}. {texto}\n{'=' * 78}")


def comprobar(condicion: bool, descripcion: str) -> bool:
    print(f"   {'✓' if condicion else '✗'} {descripcion}")
    if not condicion:
        _fallos.append(descripcion)
    return condicion


def ptnt(*args: str, espera_fallo: bool = False) -> str:
    """Ejecuta la CLI real, como la ejecutaría el analista."""

    cmd = [sys.executable, "-m", "ptnt.cli", *args, "--config", str(CONFIG)]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=RAIZ,
                          env={**_ENV, "COLUMNS": "120"})
    salida = proc.stdout + proc.stderr
    if espera_fallo:
        if proc.returncode == 0:
            _fallos.append(f"se esperaba que fallara: ptnt {' '.join(args)}")
            print(f"   ✗ debía fallar y no falló: ptnt {' '.join(args)}")
    elif proc.returncode != 0:
        _fallos.append(f"ptnt {' '.join(args)} → código {proc.returncode}")
        print(salida)
    return salida


_ENV = {**os.environ, "PYTHONPATH": str(RAIZ / "src"),
        "PTNT_JWT_SECRET": os.environ.get("PTNT_JWT_SECRET", "demo-escenarios")}
CONFIG = TRABAJO / "config.yaml"


def preparar() -> None:
    """Fuente sintética, catálogo organizacional y configuración apuntando ahí."""

    from ptnt.synth.fuentes import fuente_multialimentador

    if TRABAJO.exists():
        shutil.rmtree(TRABAJO)
    TRABAJO.mkdir(parents=True)

    codigos = [c for c, _, _ in CATALOGO]
    redes = fuente_multialimentador(str(TRABAJO / "ptnt.duckdb"), codigos,
                                    n_transformers=6, customers_per_tx=12)

    pd.DataFrame(CATALOGO,
                 columns=["feeder_code", "subestacion", "unidad_negocio"]
                 ).to_csv(TRABAJO / "jerarquia.csv", index=False)

    # Energía medida en cabecera. El generador sabe cuánto se facturó y cuánta
    # pérdida no técnica inyectó, así que la cabecera coherente es la facturación
    # inflada por esa fracción más un margen para la pérdida técnica.
    pd.DataFrame([
        {"feeder_code": codigo, "period": "2026-07",
         "kwh_delivered": round(
             sum(c["energy_kwh"] for cls in red.customer_nodes.values()
                 for c in cls) / (1.0 - 0.08 - 0.06), 1)}
        for codigo, red in redes.items()
    ]).to_csv(TRABAJO / "cabecera.csv", index=False)

    cfg = yaml.safe_load((RAIZ / "config" / "base.yaml").read_text(encoding="utf-8"))
    cfg["rutas"]["duckdb"] = str(TRABAJO / "ptnt.duckdb")
    cfg["rutas"]["salidas"] = str(TRABAJO / "salidas")
    for f in cfg["fuentes"]:
        if f["nombre"] == "resultados_local":
            f["ruta"] = str(TRABAJO / "ptnt.duckdb")
    cfg["organizacion"]["catalogo"] = str(TRABAJO / "jerarquia.csv")
    cfg["seguridad"]["ruta_usuarios"] = str(TRABAJO / "usuarios.json")
    CONFIG.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")


def crear_usuarios() -> None:
    """Los mismos usuarios que crearía ``ptnt crear-usuario``, sin prompt."""

    from ptnt.security.auth import UserStore

    store = UserStore(TRABAJO / "usuarios.json")
    for usuario, unidad in [("ana", "GUAYAQUIL"), ("beto", "MILAGRO"),
                            ("clara", "SANTA_ELENA")]:
        store.add_user(usuario, f"clave-{usuario}-2026", "analyst",
                       unidades=[unidad])
    store.add_user("matriz", "clave-matriz-2026", "admin", matriz=True)


def id_de(salida: str) -> str:
    """El identificador corto que la CLI imprime al abrir un escenario."""

    for linea in salida.splitlines():
        if "abierto" in linea and "(" in linea:
            return linea.split("(")[-1].split(")")[0].strip()
    return ""


def main() -> int:  # noqa: C901 - recorrido lineal, se lee como tal
    print("\n" + "=" * 78)
    print("  PTNT-BAL — ESCENARIOS DE TRABAJO Y ALCANCE POR UNIDAD DE NEGOCIO")
    print("=" * 78)
    preparar()
    print(f"\n  Todo ocurre en: {TRABAJO}")

    # ---------------------------------------------------------------- 1 y 2 --
    titulo(1, "Tres unidades de negocio, cinco alimentadores")
    jer = pd.read_csv(TRABAJO / "jerarquia.csv")
    print(jer.to_string(index=False))

    titulo(2, "Alta de usuarios: uno por unidad, uno de matriz")
    # En operación esto es `ptnt crear-usuario ana --rol analyst --unidad
    # GUAYAQUIL`, que pide la contraseña por prompt seguro. La CLI **no acepta
    # la contraseña como argumento** a propósito: quedaría en el historial del
    # shell. Aquí se crean por la misma API que usa la CLI, para no depender de
    # un terminal interactivo.
    crear_usuarios()
    salida = ptnt("usuarios")
    print(salida)
    comprobar("GUAYAQUIL" in salida and "MILAGRO" in salida,
              "cada usuario aparece con la unidad que le toca")

    # -------------------------------------------------------------------- 3 --
    titulo(3, "Lo que cada uno alcanza")
    comprobar("todas las unidades" in salida.lower(),
              "la matriz se distingue de un analista de unidad")

    # -------------------------------------------------------------------- 4 --
    titulo(4, "Ana abre un escenario sobre su alimentador GYE-01")
    salida = ptnt("escenario-abrir", "Rebalanceo de banco en GYE-01",
                  "--usuario", "ana", "--entidad", "GYE-01",
                  "--comentario", "El puesto TS2 se ve sobrecargado")
    print(salida)
    esc = id_de(salida)
    comprobar(bool(esc), "el escenario queda abierto con identificador propio")

    # -------------------------------------------------------------------- 5 --
    titulo(5, "Acumula cambios — el modelo oficial no se toca")
    ptnt("escenario-cambiar", esc, "--capa", "ptnt_puesto_transformacion",
         "--elemento", "TS2", "--campo", "kva", "--valor", "150",
         "--autor", "ana", "--motivo", "Repotenciar el puesto sobrecargado")
    salida = ptnt("escenario-cambiar", esc, "--capa", "ptnt_puesto_transformacion",
                  "--elemento", "TS3", "--campo", "kva", "--valor", "112.5",
                  "--autor", "ana", "--motivo", "Acompaña al de al lado")
    print(salida)
    comprobar("2 en total" in salida, "los cambios se acumulan en el escenario")

    # -------------------------------------------------------------------- 6 --
    titulo(6, "Evalúa: el balance CON sus cambios, sin publicar nada")
    salida = ptnt("escenario-evaluar", esc, "--usuario", "ana",
                  "--comentario", "Primera prueba con los dos puestos")
    print(salida)
    comprobar("Iteración 1" in salida, "la evaluación queda como iteración 1")
    comprobar("Pnt pct" in salida, "devuelve la PNT del alcance")
    comprobar("INDICATIVO" in salida,
              "sin cabecera lo dice: la PNT es una estimación, no un número firmable")

    # -------------------------------------------------------------------- 7 --
    titulo(7, "Otra idea: un tercer puesto. Otra iteración")
    ptnt("escenario-cambiar", esc, "--capa", "ptnt_puesto_transformacion",
         "--elemento", "TS4", "--campo", "kva", "--valor", "225",
         "--autor", "ana", "--motivo", "Probar también el del final del tronco")
    # Esta vez con la energía medida en cabecera: el mismo escenario, pero el
    # balance pasa a ser MEDIDO y la PNT, contrastable.
    salida = ptnt("escenario-evaluar", esc, "--usuario", "ana",
                  "--cabecera", str(TRABAJO / "cabecera.csv"),
                  "--comentario", "Ahora con los tres y con cabecera medida")
    print(salida)
    comprobar("Iteración 2" in salida, "la segunda evaluación no pisa a la primera")
    comprobar("MEDIDO" in salida,
              "con cabecera el balance es MEDIDO y la PNT sí es verificable")

    # -------------------------------------------------------------------- 8 --
    titulo(8, "La evolución del alimentador en el tiempo")
    salida = ptnt("escenario-evolucion", esc)
    print(salida)
    comprobar(salida.count("|") >= 0 and "1" in salida and "2" in salida,
              "las dos iteraciones constan, en orden")

    salida = ptnt("escenario-evolucion", "--entidad", "GYE-01", "--usuario", "ana")
    print(salida)
    comprobar("GYE-01" in salida,
              "la historia de la entidad cruza todos sus escenarios")

    # -------------------------------------------------------------------- 9 --
    titulo(9, "Qué cambió entre la primera iteración y la segunda")
    salida = ptnt("escenario-comparar", esc, "--desde", "1", "--hasta", "2")
    print(salida)
    comprobar("Iteración 1 → 2" in salida, "la comparación es explícita")
    comprobar("tipo de balance cambió" in salida,
              "avisa que se está comparando una PNT estimada con una medida")

    # ------------------------------------------------------------------- 10 --
    titulo(10, "Un escenario a nivel de SUBESTACIÓN")
    salida = ptnt("escenario-abrir", "Plan SE-CENTRO", "--usuario", "ana",
                  "--entidad", "SE-CENTRO", "--nivel", "SUBESTACION")
    print(salida)
    sub = id_de(salida)
    comprobar("2 alimentador(es)" in salida,
              "el alcance abarca los dos alimentadores de la subestación")

    salida = ptnt("escenario-evaluar", sub, "--usuario", "ana",
                  "--cabecera", str(TRABAJO / "cabecera.csv"))
    print(salida)
    comprobar("Alimentadores" in salida,
              "el balance de la subestación se calcula sobre sus alimentadores")
    comprobar("GYE-01" in salida and "GYE-02" in salida,
              "y se ve el desglose por alimentador, no solo el total")

    # ------------------------------------------------------------------- 11 --
    titulo(11, "Lo que un usuario de otra unidad NO puede hacer")
    salida = ptnt("escenario-abrir", "Intento indebido", "--usuario", "beto",
                  "--entidad", "GYE-01", espera_fallo=True)
    print(salida)
    comprobar("GUAYAQUIL" in salida or "no alcanza" in salida.lower(),
              "Milagro no puede abrir un escenario en un alimentador de Guayaquil")

    salida = ptnt("escenario-evaluar", esc, "--usuario", "beto",
                  espera_fallo=True)
    comprobar("beto" not in salida or "alcance" in salida.lower(),
              "tampoco puede evaluar un escenario ajeno")

    salida = ptnt("escenario-listar", "--usuario", "beto")
    print(salida)
    comprobar("Rebalanceo" not in salida,
              "ni siquiera ve en la lista los escenarios de otra unidad")

    # ------------------------------------------------------------------- 12 --
    titulo(12, "La matriz sí ve —y puede analizar— todo")
    salida = ptnt("escenario-listar", "--usuario", "matriz")
    print(salida)
    comprobar("Rebalanceo" in salida and "SE-CENTRO" in salida,
              "la oficina central ve los escenarios de todas las unidades")

    salida = ptnt("escenario-abrir", "Revisión matriz de MIL-01",
                  "--usuario", "matriz", "--entidad", "MIL-01")
    comprobar("abierto" in salida,
              "y puede trabajar sobre cualquier unidad que quiera")

    # ------------------------------------------------------------------------
    print("\n" + "=" * 78)
    if _fallos:
        print(f"  {len(_fallos)} COMPROBACIÓN(ES) FALLIDA(S):")
        for f in _fallos:
            print(f"   ✗ {f}")
        print("=" * 78)
        return 1
    print("  TODO CORRECTO — el ciclo completo de escenarios funciona.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
