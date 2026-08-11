"""Lo que la cuadrilla encontró vuelve al modelo (bucle de aprendizaje).

Hasta aquí el ciclo se cortaba en la revisión: el técnico capturaba
``hallazgo = MEDIDOR_MANIPULADO``, el supervisor lo aceptaba, el cambio entraba
al SIG… y el detector seguía calibrándose contra la base de multados heredada.
El sistema mandaba cuadrillas y **no aprendía de lo que las cuadrillas
encontraban**, que es la información más cara y más limpia que produce la
empresa.

Este módulo cierra ese bucle. Tiene tres piezas y una decisión que importa más
que las tres.

**La decisión: no todo lo que no es hurto es inocencia.**

De los once hallazgos posibles, solo dos grupos son etiquetas utilizables:

* Los que **confirman** el hurto (conexión directa, medidor manipulado…).
* ``SIN_NOVEDAD``, que confirma lo contrario. Es el dato más escaso del
  problema: un cliente donde *fue* un técnico, *miró* y *no había nada*. La
  literatura de PNT usa aprendizaje PU precisamente porque los negativos no
  existen; aquí sí existen, y hay que guardarlos aparte.

``PREDIO_CERRADO`` y ``ACCESO_NEGADO`` **no son ninguna de las dos cosas**.
Meterlos como negativos es envenenar el conjunto: el modelo aprendería que un
cliente sospechoso al que nadie pudo entrar es un cliente limpio, y dejaría de
señalar justo el caso donde más vale la pena insistir. Se guardan como **no
concluyentes**, y aparecen en el informe como trabajo que hay que rehacer.

La fecha que se guarda es la de la **inspección**, no la del proceso: es la que
`fecha_corte` usa para descartar fuga temporal, y ponerle la fecha de carga
haría que una señal calculada con meses posteriores «predijera» algo ya ocurrido.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path

import pandas as pd


class Veredicto(str, Enum):
    """Qué se puede afirmar de una cuenta después de la visita."""

    CONFIRMADO = "CONFIRMADO"            # se verificó el hurto
    DESCARTADO = "DESCARTADO"            # se fue, se miró y no había nada
    NO_CONCLUYENTE = "NO_CONCLUYENTE"    # no se pudo verificar: no es inocencia
    PROBLEMA_DATOS = "PROBLEMA_DATOS"    # el cliente o el dato no existen

    @property
    def es_etiqueta(self) -> bool:
        """¿Sirve para entrenar o calibrar?"""

        return self in (Veredicto.CONFIRMADO, Veredicto.DESCARTADO)


# Cómo se traduce cada hallazgo del formulario de campo a un veredicto. La
# tabla vive aquí y no en el esquema del móvil a propósito: el técnico reporta
# **lo que vio**, y qué significa eso para el modelo es una decisión analítica
# que puede cambiar sin tocar la aplicación ni volver a salir a la calle.
VEREDICTO_POR_HALLAZGO: dict[str, Veredicto] = {
    "CONEXION_DIRECTA": Veredicto.CONFIRMADO,
    "MEDIDOR_MANIPULADO": Veredicto.CONFIRMADO,
    "PUENTE_EN_ACOMETIDA": Veredicto.CONFIRMADO,
    "SELLOS_VIOLENTADOS": Veredicto.CONFIRMADO,
    "MEDIDOR_INVERTIDO": Veredicto.CONFIRMADO,
    "MEDIDOR_FRENADO": Veredicto.CONFIRMADO,
    "SIN_NOVEDAD": Veredicto.DESCARTADO,
    "PREDIO_CERRADO": Veredicto.NO_CONCLUYENTE,
    "ACCESO_NEGADO": Veredicto.NO_CONCLUYENTE,
    "CLIENTE_NO_EXISTE": Veredicto.PROBLEMA_DATOS,
    "ERROR_DE_DATOS": Veredicto.PROBLEMA_DATOS,
}


@dataclass
class ResultadoInspeccion:
    """Una visita, con lo que se puede afirmar de ella."""

    cuenta: str
    hallazgo: str
    veredicto: Veredicto
    fecha: str = ""                  # la de la INSPECCIÓN, no la de la carga
    orden_trabajo: str = ""
    tecnico: str = ""
    lectura_medidor: float | None = None
    elemento_guid: str = ""

    def to_dict(self) -> dict:
        return {
            "contract_account": self.cuenta,
            "hallazgo": self.hallazgo,
            "veredicto": self.veredicto.value,
            "fecha_inspeccion": self.fecha,
            "orden_trabajo": self.orden_trabajo,
            "tecnico": self.tecnico,
            "lectura_medidor": self.lectura_medidor,
            "elemento_guid": self.elemento_guid,
        }


@dataclass
class ResumenCampana:
    """Cómo rindió lo que se mandó a campo. El número que justifica el programa."""

    inspecciones: int = 0
    confirmados: int = 0
    descartados: int = 0
    no_concluyentes: int = 0
    problemas_datos: int = 0
    advertencias: list[str] = field(default_factory=list)

    @property
    def verificadas(self) -> int:
        """Visitas de las que se puede afirmar algo."""

        return self.confirmados + self.descartados

    @property
    def precision_campo_pct(self) -> float:
        """De cada 100 visitas **verificables**, cuántas encontraron hurto.

        Se calcula sobre las verificables y no sobre el total a propósito: si el
        30 % de los predios estaba cerrado, incluirlos hundiría el indicador y
        el equipo parecería estar apuntando mal cuando lo que tiene es un
        problema de acceso. Los dos números se reportan por separado.
        """

        return 100.0 * self.confirmados / self.verificadas if self.verificadas else 0.0

    @property
    def cobertura_pct(self) -> float:
        """Qué porcentaje de las visitas dejó una conclusión."""

        return 100.0 * self.verificadas / self.inspecciones if self.inspecciones else 0.0

    def resumen(self) -> dict:
        return {
            "inspecciones": self.inspecciones,
            "confirmados": self.confirmados,
            "descartados": self.descartados,
            "no_concluyentes": self.no_concluyentes,
            "problemas_datos": self.problemas_datos,
            "precision_campo_pct": round(self.precision_campo_pct, 1),
            "cobertura_pct": round(self.cobertura_pct, 1),
        }

    def lectura(self) -> str:
        """Lo que el resumen significa, dicho para quien decide el presupuesto."""

        if not self.inspecciones:
            return "Sin inspecciones registradas todavía."
        partes = [
            f"De {self.verificadas} visita(s) con conclusión, "
            f"{self.confirmados} encontraron hurto: "
            f"**{self.precision_campo_pct:.0f} % de acierto en campo**."
        ]
        if self.cobertura_pct < 75 and self.no_concluyentes:
            partes.append(
                f"Pero {self.no_concluyentes} visita(s) no concluyeron "
                f"({100 - self.cobertura_pct:.0f} % del total): predio cerrado o "
                "acceso negado. Eso no es un fallo del ranking, es trabajo que "
                "hay que rehacer — conviene revisar el horario de las visitas "
                "antes de tocar el detector."
            )
        return " ".join(partes)


# --------------------------------------------------------------------------- #
# Extracción desde lo que vuelve del campo
# --------------------------------------------------------------------------- #
def extraer_resultados(clientes: pd.DataFrame, *,
                       cuenta_col: str = "cuenta_contrato",
                       fecha_lote: str | None = None) -> list[ResultadoInspeccion]:
    """Lee los resultados de inspección de la capa de clientes de un paquete.

    Solo se toman las filas marcadas como **inspeccionadas**: un cliente que
    viajó en el paquete y que el técnico no llegó a visitar no es un dato, y
    contarlo como «sin novedad» inventaría un negativo que nadie verificó.
    """

    if clientes is None or clientes.empty:
        return []

    d = clientes
    if "inspeccionado" in d.columns:
        marcado = d["inspeccionado"].map(
            lambda v: str(v).strip().lower() in ("1", "true", "sí", "si", "t"))
        d = d[marcado.fillna(False)]
    if "hallazgo" in d.columns:
        d = d[d["hallazgo"].notna() & (d["hallazgo"].astype(str).str.strip() != "")]
    if d.empty:
        return []

    out: list[ResultadoInspeccion] = []
    for _, r in d.iterrows():
        hallazgo = str(r.get("hallazgo", "")).strip().upper()
        cuenta = str(r.get(cuenta_col, "") or "").strip()
        if not cuenta:
            continue
        out.append(ResultadoInspeccion(
            cuenta=cuenta,
            hallazgo=hallazgo,
            # Un hallazgo que no está en la tabla no se descarta ni se asume
            # bueno: entra como no concluyente y aparece en las advertencias.
            veredicto=VEREDICTO_POR_HALLAZGO.get(hallazgo, Veredicto.NO_CONCLUYENTE),
            fecha=_fecha(r.get("fecha_inspeccion"), fecha_lote),
            orden_trabajo=str(r.get("orden_trabajo", "") or ""),
            tecnico=str(r.get("editado_por", "") or ""),
            lectura_medidor=_num(r.get("lectura_medidor")),
            elemento_guid=str(r.get("guid", "") or ""),
        ))
    return out


def _fecha(valor, respaldo: str | None) -> str:
    for candidato in (valor, respaldo):
        if candidato in (None, "") or (isinstance(candidato, float) and pd.isna(candidato)):
            continue
        f = pd.to_datetime(candidato, errors="coerce")
        if not pd.isna(f):
            return str(f.date())
    return ""


def _num(v) -> float | None:
    n = pd.to_numeric(v, errors="coerce")
    return None if pd.isna(n) else float(n)


# --------------------------------------------------------------------------- #
# Registro acumulado
# --------------------------------------------------------------------------- #
class RegistroInspecciones:
    """Lo que las cuadrillas han encontrado, acumulado entre campañas.

    Es el activo que el sistema construye con el tiempo: cada mes que se sale a
    campo, la base propia de casos verificados crece y el detector se calibra
    contra la realidad local en vez de contra una base heredada.

    La clave es ``(cuenta, orden_trabajo)`` y no la cuenta sola: **un mismo
    cliente puede inspeccionarse varias veces** —se le encontró hurto, se
    normalizó, se volvió a revisar seis meses después—, y cada visita es un dato
    distinto. Quedarse solo con la última perdería la historia justo de los
    clientes reincidentes, que son los que más importan.
    """

    COLUMNAS = ["contract_account", "hallazgo", "veredicto", "fecha_inspeccion",
                "orden_trabajo", "tecnico", "lectura_medidor", "elemento_guid",
                "registrado_en"]

    def __init__(self, ruta: str | Path):
        self.ruta = Path(ruta)
        self.df = self._cargar()

    def _cargar(self) -> pd.DataFrame:
        if not self.ruta.exists():
            return pd.DataFrame(columns=self.COLUMNAS)
        try:
            if self.ruta.suffix == ".parquet":
                d = pd.read_parquet(self.ruta)
            else:
                d = pd.read_csv(self.ruta, dtype={"contract_account": str})
        except Exception:
            return pd.DataFrame(columns=self.COLUMNAS)
        for c in self.COLUMNAS:
            if c not in d.columns:
                d[c] = None
        d["contract_account"] = d["contract_account"].astype(str)
        return d[self.COLUMNAS]

    def registrar(self, resultados: list[ResultadoInspeccion]) -> ResumenCampana:
        """Incorpora los resultados de una campaña. Es idempotente."""

        res = ResumenCampana(inspecciones=len(resultados))
        if not resultados:
            res.advertencias.append("El lote no trae inspecciones con hallazgo.")
            return res

        ahora = datetime.now(timezone.utc).isoformat(timespec="seconds")
        filas = []
        desconocidos: set[str] = set()
        for r in resultados:
            if r.hallazgo not in VEREDICTO_POR_HALLAZGO:
                desconocidos.add(r.hallazgo)
            if r.veredicto is Veredicto.CONFIRMADO:
                res.confirmados += 1
            elif r.veredicto is Veredicto.DESCARTADO:
                res.descartados += 1
            elif r.veredicto is Veredicto.PROBLEMA_DATOS:
                res.problemas_datos += 1
            else:
                res.no_concluyentes += 1
            filas.append({**r.to_dict(), "registrado_en": ahora})

        if desconocidos:
            # Se avisa en vez de fallar: un hallazgo nuevo en el formulario no
            # puede tumbar la carga del trabajo del día. Pero tampoco puede
            # entrar como etiqueta sin que un analista decida qué significa.
            res.advertencias.append(
                f"Hallazgo(s) sin traducción a veredicto: "
                f"{', '.join(sorted(desconocidos))}. Se registran como NO "
                "CONCLUYENTE y no se usan para calibrar hasta decidir qué "
                "significan.")

        nuevo = pd.DataFrame(filas)
        self.df = pd.concat([self.df, nuevo], ignore_index=True)
        # Idempotencia: reprocesar el mismo lote no duplica la visita.
        self.df = self.df.drop_duplicates(
            subset=["contract_account", "orden_trabajo", "fecha_inspeccion"],
            keep="last").reset_index(drop=True)

        if not res.verificadas:
            res.advertencias.append(
                "Ninguna visita dejó conclusión: no hay nada que aprender de "
                "esta campaña.")
        return res

    def guardar(self) -> Path:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        if self.ruta.suffix == ".parquet":
            self.df.to_parquet(self.ruta, index=False)
        else:
            self.df.to_csv(self.ruta, index=False)
        return self.ruta

    # -- lo que el modelo consume ------------------------------------------
    def base_confirmados(self, *, incluir_heredados: pd.DataFrame | None = None
                         ) -> pd.DataFrame:
        """Casos confirmados en el formato que espera :func:`load_confirmed_theft`.

        ``incluir_heredados`` permite unir la base histórica de multados con lo
        verificado en campo. Se unen y no se reemplazan: la base heredada es
        peor —no se sabe cómo se eligieron esos casos— pero mientras la propia
        sea pequeña, tirarla sería perder señal.
        """

        propios = self.df[self.df["veredicto"] == Veredicto.CONFIRMADO.value]
        salida = pd.DataFrame({
            "contract_account": propios["contract_account"].astype(str),
            "fecha_multa": propios["fecha_inspeccion"],
            "tipo_hallazgo": propios["hallazgo"],
            "origen": "CAMPO",
        })
        if incluir_heredados is not None and not incluir_heredados.empty:
            h = incluir_heredados.copy()
            h["contract_account"] = h["contract_account"].astype(str)
            if "origen" not in h.columns:
                h["origen"] = "HISTORICO"
            salida = pd.concat([h, salida], ignore_index=True)
        return salida.drop_duplicates(
            subset=["contract_account", "fecha_multa"]).reset_index(drop=True)

    def negativos_verificados(self) -> set[str]:
        """Cuentas donde **fue** un técnico, miró y no había nada.

        Es el dato más escaso del problema. El aprendizaje PU existe porque los
        negativos no se conocen; estos sí se conocen, y permiten medir la tasa
        de falsos positivos de verdad en vez de estimarla.
        """

        return set(self.df.loc[
            self.df["veredicto"] == Veredicto.DESCARTADO.value,
            "contract_account"].astype(str))

    def sin_concluir(self) -> pd.DataFrame:
        """Visitas que no dejaron conclusión: trabajo a rehacer, no inocentes."""

        return self.df[
            self.df["veredicto"] == Veredicto.NO_CONCLUYENTE.value
        ].reset_index(drop=True)

    def resumen_acumulado(self) -> ResumenCampana:
        r = ResumenCampana(inspecciones=len(self.df))
        conteo = self.df["veredicto"].value_counts().to_dict()
        r.confirmados = int(conteo.get(Veredicto.CONFIRMADO.value, 0))
        r.descartados = int(conteo.get(Veredicto.DESCARTADO.value, 0))
        r.no_concluyentes = int(conteo.get(Veredicto.NO_CONCLUYENTE.value, 0))
        r.problemas_datos = int(conteo.get(Veredicto.PROBLEMA_DATOS.value, 0))
        return r

    def rendimiento_por_campana(self) -> pd.DataFrame:
        """Acierto por orden de trabajo: qué campañas rindieron y cuáles no."""

        if self.df.empty:
            return pd.DataFrame()
        d = self.df.copy()
        d["_conf"] = (d["veredicto"] == Veredicto.CONFIRMADO.value).astype(int)
        d["_verif"] = d["veredicto"].isin(
            [Veredicto.CONFIRMADO.value, Veredicto.DESCARTADO.value]).astype(int)
        g = d.groupby("orden_trabajo").agg(
            visitas=("contract_account", "count"),
            verificadas=("_verif", "sum"),
            confirmados=("_conf", "sum"),
        ).reset_index()
        g["precision_pct"] = (100.0 * g["confirmados"] /
                              g["verificadas"].replace(0, pd.NA)).round(1)
        return g.sort_values("confirmados", ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Comparación entre lo que el modelo predijo y lo que el campo encontró
# --------------------------------------------------------------------------- #
@dataclass
class ContrasteRanking:
    """Cuánto acertó el ranking, medido contra visitas reales."""

    inspeccionados: int = 0
    en_ranking: int = 0
    precision_top_pct: float = 0.0
    precision_resto_pct: float = 0.0
    lift_campo: float = 0.0
    advertencias: list[str] = field(default_factory=list)

    def resumen(self) -> dict:
        return {
            "inspeccionados_verificados": self.inspeccionados,
            "de_ellos_en_el_top_del_ranking": self.en_ranking,
            "precision_en_el_top_pct": round(self.precision_top_pct, 1),
            "precision_fuera_del_top_pct": round(self.precision_resto_pct, 1),
            "lift_medido_en_campo": round(self.lift_campo, 2),
        }


def contrastar_con_ranking(registro: RegistroInspecciones, ranking: pd.DataFrame,
                           *, top_pct: float = 10.0,
                           cuenta_col: str = "contract_account",
                           rank_col: str = "rank") -> ContrasteRanking:
    """Compara lo que el ranking dijo con lo que la cuadrilla encontró.

    Es la única medición de calidad que no depende de supuestos: no compara
    contra una base heredada de procedencia desconocida, sino contra visitas que
    esta empresa hizo, en estas calles, este año.

    El lift se calcula solo sobre visitas **verificadas**. Incluir los predios
    cerrados lo hundiría, y estaría midiendo la logística de las visitas en vez
    de la calidad del ranking.
    """

    out = ContrasteRanking()
    verificados = registro.df[registro.df["veredicto"].isin(
        [Veredicto.CONFIRMADO.value, Veredicto.DESCARTADO.value])]
    if verificados.empty or ranking is None or ranking.empty:
        out.advertencias.append(
            "Sin visitas verificadas o sin ranking: no hay contraste posible.")
        return out

    r = ranking.copy()
    r[cuenta_col] = r[cuenta_col].astype(str)
    corte = max(1, int(len(r) * top_pct / 100.0))
    top = set(r.nsmallest(corte, rank_col)[cuenta_col]) if rank_col in r.columns \
        else set(r.head(corte)[cuenta_col])

    v = verificados.copy()
    v["contract_account"] = v["contract_account"].astype(str)
    v["_hurto"] = (v["veredicto"] == Veredicto.CONFIRMADO.value).astype(int)
    v["_top"] = v["contract_account"].isin(top)

    out.inspeccionados = len(v)
    out.en_ranking = int(v["_top"].sum())
    en_top, fuera = v[v["_top"]], v[~v["_top"]]
    out.precision_top_pct = 100.0 * en_top["_hurto"].mean() if len(en_top) else 0.0
    out.precision_resto_pct = 100.0 * fuera["_hurto"].mean() if len(fuera) else 0.0

    base = v["_hurto"].mean()
    out.lift_campo = (out.precision_top_pct / 100.0 / base) if base > 0 else 0.0

    if len(en_top) < 20:
        # Con pocos casos el número es ruido, y presentarlo sin este aviso
        # invitaría a tomar decisiones de presupuesto sobre nada.
        out.advertencias.append(
            f"Solo {len(en_top)} visita(s) verificadas dentro del top {top_pct:.0f} %: "
            "el lift medido todavía no es estable. Hacen falta más campañas.")
    if not len(fuera):
        out.advertencias.append(
            "Todas las visitas verificadas salieron del top del ranking. Sin "
            "inspecciones fuera de él no hay grupo de comparación, y el lift "
            "queda sobreestimado: conviene reservar una fracción de las visitas "
            "a clientes tomados al azar.")
    return out


def guardar_resumen(ruta: str | Path, resumen: ResumenCampana,
                    contraste: ContrasteRanking | None = None) -> Path:
    """Deja el resultado en JSON para el tablero y el histórico."""

    p = Path(ruta)
    p.parent.mkdir(parents=True, exist_ok=True)
    datos = {"generado_en": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "campana": resumen.resumen(), "lectura": resumen.lectura(),
             "advertencias": resumen.advertencias}
    if contraste is not None:
        datos["contraste_con_ranking"] = contraste.resumen()
        datos["advertencias"] = datos["advertencias"] + contraste.advertencias
    p.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    return p
