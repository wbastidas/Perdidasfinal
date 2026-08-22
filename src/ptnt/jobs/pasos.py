"""Los pasos del proceso, descritos para quien no sabe qué es un alimentador.

Este catálogo existe para que la interfaz no tenga que saber nada del proceso.
Cada paso trae **su título en lenguaje llano, para qué sirve, qué necesita antes
y qué produce**, de modo que el tablero pueda pintar botones y explicaciones sin
que nadie tenga que escribir un comando.

**Un paso se ejecuta invocando la CLI real, no reimplementando lo que hace.** Es
la decisión que sostiene el módulo: el botón del tablero, la tarea programada de
las tres de la mañana y lo que un técnico teclea en la consola son literalmente
la misma orden. Cualquier otro arreglo produce, tarde o temprano, un tablero que
calcula distinto que la madrugada.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Opcion:
    """Una opción que el usuario puede ajustar antes de lanzar el paso."""

    clave: str
    bandera: str                     # cómo se llama en la CLI: "--csv"
    titulo: str
    tipo: str = "texto"              # texto | numero | archivo | si_no
    por_defecto: object = ""
    ayuda: str = ""
    obligatoria: bool = False


@dataclass(frozen=True)
class Paso:
    """Un paso del proceso, tal como se le presenta al usuario."""

    clave: str
    titulo: str
    para_que: str
    comando: str
    opciones: tuple[Opcion, ...] = ()
    necesita: tuple[str, ...] = ()
    produce: str = ""
    minutos_tipicos: int = 1

    def argumentos(self, valores: dict) -> list[str]:
        """Traduce lo que eligió el usuario a argumentos de la línea de comandos."""

        args = [self.comando]
        for op in self.opciones:
            v = valores.get(op.clave, op.por_defecto)
            if v is None or v == "":
                if op.obligatoria:
                    raise ValueError(
                        f"El paso «{self.titulo}» necesita {op.titulo.lower()} "
                        "y no se indicó.")
                continue
            if op.tipo == "si_no":
                # Una bandera booleana se pone o no se pone; pasarle "False"
                # como texto haría que typer la leyera como activada.
                if bool(v):
                    args.append(op.bandera)
                continue
            args.extend([op.bandera, str(v)])
        return args


# --------------------------------------------------------------------------- #
# El catálogo
# --------------------------------------------------------------------------- #
_CFG = ()   # la ruta de configuración la añade el ejecutor, no cada paso

PASOS: tuple[Paso, ...] = (
    Paso(
        clave="verificar",
        titulo="Comprobar que todo está en su sitio",
        para_que="Revisa que la configuración sea válida antes de tocar nada. "
                 "Si falta un parámetro, lo dice por su nombre en vez de fallar "
                 "a mitad del proceso.",
        comando="verificar-config",
        produce="Un visto bueno, o el nombre exacto de lo que falta.",
        minutos_tipicos=1,
    ),
    Paso(
        clave="fuentes",
        titulo="Probar la conexión con las bases",
        para_que="Comprueba que se puede llegar a cada base de origen "
                 "(comercial, SIG) con las credenciales configuradas.",
        comando="probar-fuentes",
        produce="Una base por fila, con su estado.",
        minutos_tipicos=1,
    ),
    Paso(
        clave="migrar",
        titulo="Traer la red desde el SIG",
        para_que="Lee la red del sistema de información geográfica y la convierte "
                 "al modelo interno: tramos, transformadores, clientes y luminarias.",
        comando="migrar",
        opciones=(
            Opcion("feeder", "--feeder", "Alimentador (vacío = todos)",
                   ayuda="Deje vacío para traer lo que haya en la fuente."),
        ),
        necesita=("verificar",),
        produce="La red en el modelo interno, versionada.",
        minutos_tipicos=5,
    ),
    Paso(
        clave="analizar",
        titulo="Analizar el consumo de los clientes",
        para_que="Promedia el consumo de los últimos meses, recalcula la potencia "
                 "de cada cliente y busca comportamientos compatibles con hurto.",
        comando="analizar",
        opciones=(
            Opcion("csv", "--csv", "Archivo de consumos", tipo="archivo",
                   ayuda="El CSV comercial con la historia de consumo."),
        ),
        necesita=("verificar",),
        produce="Ranking de clientes sospechosos y reconciliación de potencia.",
        minutos_tipicos=10,
    ),
    Paso(
        clave="analizar_red",
        titulo="Calcular el balance y las pérdidas",
        para_que="Calcula el flujo de potencia, las pérdidas técnicas y, por "
                 "diferencia con lo facturado, la pérdida no técnica.",
        comando="analizar-red",
        opciones=(
            Opcion("trifasico", "--trifasico", "Motor trifásico con neutro",
                   tipo="si_no", por_defecto=True,
                   ayuda="Más preciso en redes desbalanceadas. Tarda algo más."),
        ),
        necesita=("migrar",),
        produce="Balance por alimentador y pérdidas desglosadas.",
        minutos_tipicos=15,
    ),
    Paso(
        clave="diagnostico",
        titulo="Comprobar si el resultado es creíble",
        para_que="Antes de mandar a nadie a la calle: busca transferencias de "
                 "carga no reportadas, clientes que facturan sin estar en el SIG "
                 "y alimentadores cuyo número no cierra.",
        comando="diagnostico",
        opciones=(
            Opcion("cabecera", "--cabecera", "Medición de cabecera", tipo="archivo"),
            Opcion("multados", "--multados", "Base de multados", tipo="archivo"),
        ),
        necesita=("analizar_red",),
        produce="Informe de credibilidad y precisión real del detector.",
        minutos_tipicos=5,
    ),
    Paso(
        clave="focalizar",
        titulo="Decidir dónde ir a inspeccionar",
        para_que="Ordena alimentadores, ramales, transformadores, sectores y "
                 "clientes por lo que se puede recuperar en cada visita.",
        comando="focalizar",
        necesita=("analizar", "analizar_red"),
        produce="Plan de levantamientos con órdenes de trabajo.",
        minutos_tipicos=5,
    ),
    Paso(
        clave="consolidar",
        titulo="Consolidar por unidad y subestación",
        para_que="Suma hacia arriba y guarda el punto del período en el histórico, "
                 "marcando el consolidado como estimado si algún alimentador lo es.",
        comando="consolidar",
        necesita=("analizar_red",),
        produce="Consolidado por unidad de negocio y un punto más en el histórico.",
        minutos_tipicos=2,
    ),
)

POR_CLAVE = {p.clave: p for p in PASOS}

# Lo que se ejecuta cuando alguien pulsa «actualizar todo» o cuando corre la
# tarea de la madrugada. El orden importa: cada paso consume lo del anterior.
PLAN_COMPLETO = ("verificar", "fuentes", "migrar", "analizar", "analizar_red",
                 "diagnostico", "focalizar", "consolidar")

# Actualización periódica sin diagnóstico ni focalización: solo refrescar los
# números. Es lo que suele querer una tarea diaria.
PLAN_ACTUALIZAR = ("migrar", "analizar", "analizar_red", "consolidar")

PLANES = {
    "completo": ("Proceso completo", PLAN_COMPLETO,
                 "Todo, de la conexión a las órdenes de campo."),
    "actualizar": ("Actualizar los números", PLAN_ACTUALIZAR,
                   "Refresca datos y balance. No rehace el plan de campo."),
    "balance": ("Solo el balance", ("migrar", "analizar_red"),
                "Cuando cambió la red y se quiere ver el efecto."),
    "campo": ("Preparar trabajo de campo", ("diagnostico", "focalizar"),
              "Comprueba credibilidad y decide dónde ir."),
}


def resolver_plan(claves: list[str]) -> list[Paso]:
    """Ordena los pasos pedidos, en el orden en que deben ocurrir."""

    pedidos = [c for c in claves if c]
    desconocidos = [c for c in pedidos if c not in POR_CLAVE]
    if desconocidos:
        raise ValueError(
            f"Paso(s) que no existen: {', '.join(desconocidos)}. "
            f"Los que hay son: {', '.join(POR_CLAVE)}.")

    orden = {p.clave: i for i, p in enumerate(PASOS)}
    pedidos.sort(key=lambda c: orden[c])
    return [POR_CLAVE[c] for c in pedidos]


def supuestos_previos(claves: list[str]) -> list[str]:
    """Qué da por hecho este plan que se hizo antes.

    **Es un aviso, no un error.** Lo que un paso necesita es el *resultado* del
    anterior, y ese resultado puede venir de la corrida de ayer: preparar el
    trabajo de campo un martes se apoya en el balance que se calculó el lunes, y
    obligar a recalcularlo todo cada vez convertiría un plan de cinco minutos en
    uno de cuarenta.

    Si de verdad falta el insumo, el paso falla al ejecutarse diciendo cuál es
    —esos comandos ya lo hacen—, que es el momento en que se sabe de cierto.
    """

    elegidos = {c for c in claves if c in POR_CLAVE}
    avisos = []
    for clave in elegidos:
        for req in POR_CLAVE[clave].necesita:
            if req not in elegidos:
                avisos.append(
                    f"«{POR_CLAVE[clave].titulo}» usa lo que produce "
                    f"«{POR_CLAVE[req].titulo}»: se dará por hecho que ya se "
                    "ejecutó antes.")
    return sorted(set(avisos))


def duracion_estimada(pasos: list[Paso]) -> int:
    """Minutos que cabe esperar. Sirve para decidir si da tiempo antes de una
    reunión, que es la pregunta real que se hace quien pulsa el botón."""

    return sum(p.minutos_tipicos for p in pasos)
