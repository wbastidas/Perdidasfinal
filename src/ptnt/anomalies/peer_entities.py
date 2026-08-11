"""Alimentadores, ramales, sectores y rutas comparados **contra los que se les parecen**.

El sistema ya compara cada cliente contra clientes equivalentes: es lo que
convierte «consume poco» en «consume poco *para lo que es*». Este módulo sube esa
misma idea un piso, hasta las entidades agregadas.

La pregunta que responde es la que un jefe de pérdidas hace en voz alta mirando
un tablero: *«este alimentador tiene 9 % de PNT… ¿es mucho?»*. Sin referencia no
se puede contestar. Un alimentador rural largo, con pocos clientes y mucha red,
tiene pérdidas altas por física; uno urbano compacto con la misma cifra tiene un
problema. Compararlos contra el promedio de la empresa mezcla los dos casos y
produce el peor resultado posible: cuadrillas mandadas a alimentadores que están
como deben estar, y alimentadores realmente malos que pasan por normales porque
el promedio los tapa.

**La regla que hace que esto no sea circular.** El perfil que define quién se
parece a quién tiene que ser **exógeno a la métrica evaluada**. Si se buscaran
parecidos por PNT y después se evaluara la PNT, todos serían normales dentro de
su grupo por construcción — el mismo error que en la segmentación de clientes
hizo caer el lift de S5 de 10,0× a 2,4× cuando el estrato de consumo entraba en
la clave del grupo par. Aquí el perfil es **estructural**: tamaño, mezcla de
clases, densidad de red, tipo de zona.

**Y una advertencia que va en el informe, no en el código.** Que una entidad se
salga de su grupo no prueba hurto: prueba que **se comporta distinto de lo que
explicaría su estructura**. Puede ser hurto, un transformador mal asignado, un
cliente no vinculado o una maniobra no reportada. Es una pregunta bien hecha, no
una respuesta.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class EntidadAtipica:
    """Una entidad que se aparta de las que se le parecen."""

    entidad: str
    nivel: str
    observado: float
    esperado: float          # mediana de sus pares
    desviacion: float        # observado - esperado
    z_robusto: float         # en unidades de dispersión del grupo
    n_pares: int
    pares: list[str] = field(default_factory=list)
    perfil: dict = field(default_factory=dict)

    @property
    def direccion(self) -> str:
        return "ALTA" if self.desviacion > 0 else "BAJA"

    @property
    def severidad(self) -> str:
        z = abs(self.z_robusto)
        if z >= 4.5:
            return "MUY_ATIPICA"
        if z >= 3.0:
            return "ATIPICA"
        return "LIMITE"

    def descripcion(self, unidad: str = "%") -> str:
        comparacion = "por encima de" if self.desviacion > 0 else "por debajo de"
        return (
            f"{self.entidad}: {self.observado:,.1f}{unidad} frente a "
            f"{self.esperado:,.1f}{unidad} en {self.n_pares} entidad(es) "
            f"parecidas — {abs(self.desviacion):,.1f}{unidad} {comparacion} lo que "
            f"su estructura explicaría ({self.z_robusto:+.1f} desviaciones)."
        )


@dataclass
class ReporteEntidadesPares:
    nivel: str
    metrica: str
    atipicas: list[EntidadAtipica] = field(default_factory=list)
    n_evaluadas: int = 0
    n_sin_grupo: int = 0
    advertencias: list[str] = field(default_factory=list)

    @property
    def altas(self) -> list[EntidadAtipica]:
        """Las que pierden más de lo que su estructura explica: donde ir."""

        return [a for a in self.atipicas if a.desviacion > 0]

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame([
            {
                "entidad": a.entidad, "nivel": a.nivel,
                "observado": round(a.observado, 2),
                "esperado_por_sus_pares": round(a.esperado, 2),
                "desviacion": round(a.desviacion, 2),
                "z_robusto": round(a.z_robusto, 2),
                "direccion": a.direccion, "severidad": a.severidad,
                "n_pares": a.n_pares,
                "pares": ", ".join(a.pares[:5]),
            }
            for a in self.atipicas
        ])

    def resumen(self) -> dict:
        return {
            "nivel": self.nivel, "metrica": self.metrica,
            "evaluadas": self.n_evaluadas,
            "atipicas": len(self.atipicas),
            "atipicas_altas": len(self.altas),
            "sin_grupo_par": self.n_sin_grupo,
        }


def comparar_contra_pares(
    entidades: pd.DataFrame,
    *,
    nivel: str,
    columna_id: str,
    columna_metrica: str,
    columnas_perfil: list[str],
    columnas_categoricas: list[str] | None = None,
    k_pares: int = 8,
    min_pares: int = 4,
    z_minimo: float = 3.0,
) -> ReporteEntidadesPares:
    """Marca las entidades cuya métrica no encaja con la de sus parecidas.

    ``columnas_perfil`` define el parecido y **no puede incluir la métrica ni
    nada derivado de ella**: sería circular. ``columnas_categoricas`` —la unidad
    de negocio, el tipo de zona— actúan como bloque: solo se comparan entidades
    que coinciden en ellas, que es la forma directa de que el modelo «aprenda»
    que cada unidad de negocio se comporta distinto sin necesidad de estimar un
    efecto por unidad con pocos datos.

    Se usa **mediana y desviación absoluta mediana** del grupo, no media y
    desviación típica: si en un grupo de ocho hay dos con hurto grave, la media
    se va con ellos y los dos dejan de parecer atípicos — justo los que se
    buscaban.
    """

    rep = ReporteEntidadesPares(nivel=nivel, metrica=columna_metrica)

    if entidades is None or entidades.empty:
        rep.advertencias.append("Sin entidades que evaluar.")
        return rep

    circulares = [c for c in columnas_perfil if c == columna_metrica]
    if circulares:
        # No se corrige en silencio: un perfil contaminado produce un informe que
        # parece correcto y dice que todo es normal.
        rep.advertencias.append(
            f"La métrica evaluada «{columna_metrica}» estaba en el perfil de "
            "similitud. Se excluye: buscar parecidos por la misma variable que "
            "se evalúa hace que nada resulte atípico, por construcción.")
        columnas_perfil = [c for c in columnas_perfil if c != columna_metrica]

    perfil_util = [c for c in columnas_perfil if c in entidades.columns]
    if not perfil_util:
        rep.advertencias.append(
            "Ninguna columna de perfil disponible: sin estructura con la que "
            "definir el parecido, la comparación sería contra el promedio de la "
            "empresa, que es justo lo que este análisis viene a evitar.")
        return rep

    d = entidades.dropna(subset=[columna_metrica]).copy()
    d[columna_id] = d[columna_id].astype(str)
    rep.n_evaluadas = len(d)
    if len(d) < min_pares + 1:
        rep.advertencias.append(
            f"Solo {len(d)} entidad(es) en el nivel {nivel}: hacen falta al menos "
            f"{min_pares + 1} para que un grupo par signifique algo.")
        return rep

    # Perfil normalizado de forma robusta: una entidad enorme no puede dominar
    # la distancia solo por su escala.
    X = d[perfil_util].apply(pd.to_numeric, errors="coerce").astype(float)
    X = X.fillna(X.median())
    escala = X.apply(_mad_serie)
    escala = escala.replace(0, np.nan).fillna(X.std().replace(0, 1.0)).fillna(1.0)
    Z = ((X - X.median()) / escala).to_numpy(dtype=float)

    bloques = _bloques(d, columnas_categoricas)
    metrica = d[columna_metrica].astype(float).to_numpy()
    ids = d[columna_id].tolist()

    for i in range(len(d)):
        candidatos = [j for j in bloques[i] if j != i]
        if len(candidatos) < min_pares:
            rep.n_sin_grupo += 1
            continue

        dist = np.linalg.norm(Z[candidatos] - Z[i], axis=1)
        orden = np.argsort(dist)
        vecinos = _vecinos_de_verdad(
            [candidatos[j] for j in orden], dist[orden], k_pares, min_pares)
        if len(vecinos) < min_pares:
            rep.n_sin_grupo += 1
            continue

        valores = metrica[vecinos]
        esperado = float(np.median(valores))
        # Suelo de dispersión relativo al nivel del propio grupo: en un grupo de
        # seis muy parecidos la desviación mediana puede salir casi cero, y
        # entonces media décima de punto de PNT se convierte en «cuatro
        # desviaciones». Estadísticamente cierto e inútil: nadie manda una
        # cuadrilla por 0,4 puntos. Se declara que no se resuelve mejor que un
        # 10 % del nivel del grupo.
        dispersion = max(_mad(valores), 0.10 * abs(esperado))
        if dispersion <= 0:
            # Grupo idéntico y nivel cero: no hay nada que resolver.
            continue

        z = (metrica[i] - esperado) / dispersion
        if abs(z) < z_minimo:
            continue

        rep.atipicas.append(EntidadAtipica(
            entidad=ids[i], nivel=nivel,
            observado=float(metrica[i]), esperado=esperado,
            desviacion=float(metrica[i] - esperado), z_robusto=float(z),
            n_pares=len(vecinos), pares=[ids[j] for j in vecinos[:5]],
            perfil={c: _val(d.iloc[i][c]) for c in perfil_util},
        ))

    rep.atipicas.sort(key=lambda a: -abs(a.z_robusto))
    if rep.n_sin_grupo:
        rep.advertencias.append(
            f"{rep.n_sin_grupo} entidad(es) sin grupo par suficiente: son las que "
            "no se parecen a nada del universo cargado. No es que estén bien, es "
            "que no se pueden evaluar así — revíselas por su cuenta.")
    return rep


def _vecinos_de_verdad(ordenados: list[int], distancias: np.ndarray,
                       k: int, minimo: int) -> list[int]:
    """Los k más cercanos, **descartando los que ya no se parecen**.

    Pedir «los 8 más cercanos» en un universo con 6 urbanos y 6 rurales le mete
    3 rurales al grupo de un urbano, porque son los que quedan. Y entonces el
    urbano se juzga contra una mediana que arrastra la física de otra red: los
    dos alimentadores urbanos realmente malos dejan de parecer atípicos.

    El corte es **relativo**, no un umbral fijo: se toma la distancia de los
    `minimo` más cercanos como referencia de «esto es parecido» y se admite
    hasta el triple. Así se adapta a niveles donde las entidades son muy
    homogéneas y a otros donde no.
    """

    if len(ordenados) <= minimo:
        return ordenados

    referencia = float(np.median(distancias[:minimo])) if minimo else 0.0
    if referencia <= 0:
        # Varias entidades idénticas al sujeto: el corte relativo no aplica.
        return ordenados[:max(k, minimo)]

    tope = 3.0 * referencia
    admitidos = [idx for idx, dd in zip(ordenados[:k], distancias[:k]) if dd <= tope]
    # Nunca menos del mínimo: preferir un grupo algo peor a no evaluar nada,
    # pero solo hasta ahí.
    return admitidos if len(admitidos) >= minimo else ordenados[:minimo]


def _bloques(d: pd.DataFrame, categoricas: list[str] | None) -> list[list[int]]:
    """Índices comparables entre sí, agrupados por las categóricas.

    Bloquear por unidad de negocio es la manera honesta de reconocer que cada
    una tiene su propio nivel: en vez de estimar un efecto por unidad con pocos
    datos, simplemente **no se comparan entre sí**. Si una unidad queda con muy
    pocas entidades, se cae al universo completo avisando, porque un grupo par
    de dos no es un grupo par.
    """

    n = len(d)
    usables = [c for c in (categoricas or []) if c in d.columns]
    if not usables:
        return [list(range(n))] * n

    clave = d[usables].astype(str).agg("|".join, axis=1)
    por_clave: dict[str, list[int]] = {}
    for i, k in enumerate(clave):
        por_clave.setdefault(k, []).append(i)

    todos = list(range(n))
    return [por_clave[k] if len(por_clave[k]) >= 5 else todos for k in clave]


def _mad(v: np.ndarray) -> float:
    v = np.asarray(v, dtype=float)
    v = v[np.isfinite(v)]
    if v.size < 2:
        return 0.0
    return float(1.4826 * np.median(np.abs(v - np.median(v))))


def _mad_serie(s: pd.Series) -> float:
    return _mad(s.to_numpy(dtype=float))


def _val(v):
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return round(float(v), 3)
    return v


# --------------------------------------------------------------------------- #
# Perfiles listos para los niveles del modelo
# --------------------------------------------------------------------------- #
# Qué describe la estructura de cada nivel. Ninguna de estas columnas puede
# derivarse de la PNT: son el «para lo que es» contra el que se juzga la métrica.
PERFIL_POR_NIVEL: dict[str, dict] = {
    "ALIMENTADOR": {
        "perfil": ["clientes", "energia_kwh_mes", "km_red", "transformadores",
                   "kva_instalado", "pct_residencial", "kwh_por_cliente"],
        "categoricas": ["unidad_negocio"],
    },
    "RAMAL": {
        "perfil": ["clientes", "energia_kwh_mes", "km_red", "kwh_por_cliente"],
        "categoricas": ["alimentador"],
    },
    "PUESTO_TRANSFORMACION": {
        "perfil": ["clientes", "energia_kwh_mes", "kva_instalado",
                   "kwh_por_cliente", "pct_residencial"],
        "categoricas": ["alimentador"],
    },
    "RUTA_COMERCIAL": {
        "perfil": ["clientes", "energia_kwh_mes", "kwh_por_cliente",
                   "pct_residencial"],
        "categoricas": ["unidad_negocio"],
    },
    "SECTOR": {
        "perfil": ["clientes", "energia_kwh_mes", "kwh_por_cliente", "radio_m"],
        "categoricas": ["alimentador"],
    },
}


def analizar_niveles(
    por_nivel: dict[str, pd.DataFrame],
    *,
    columna_metrica: str = "pnt_pct",
    columna_id: str = "entidad",
    k_pares: int = 8,
    z_minimo: float = 3.0,
) -> dict[str, ReporteEntidadesPares]:
    """Aplica la comparación a todos los niveles que se le den, con su perfil.

    Se devuelve un reporte por nivel y no uno solo mezclado: un sector y un
    alimentador no son comparables ni entre sí ni en la misma escala, y
    juntarlos en una tabla invitaría a ordenar por desviación y mandar la
    cuadrilla al primero de la lista.
    """

    salida: dict[str, ReporteEntidadesPares] = {}
    for nivel, df in por_nivel.items():
        cfg = PERFIL_POR_NIVEL.get(nivel, {})
        salida[nivel] = comparar_contra_pares(
            df, nivel=nivel, columna_id=columna_id,
            columna_metrica=columna_metrica,
            columnas_perfil=cfg.get("perfil", []),
            columnas_categoricas=cfg.get("categoricas"),
            k_pares=k_pares, z_minimo=z_minimo,
        )
    return salida


def entidades_similares(entidades: pd.DataFrame, entidad: str, *,
                        nivel: str = "RUTA_COMERCIAL",
                        columna_id: str = "entidad",
                        k: int = 8) -> pd.DataFrame:
    """Las entidades que más se parecen a una dada, con su métrica al lado.

    Sirve para la pregunta inversa, que es la que se hace en una reunión: «esta
    ruta va mal — ¿cómo están las rutas que se le parecen?». Ver las cinco
    parecidas con sus números al lado convence más que un z-score.
    """

    cfg = PERFIL_POR_NIVEL.get(nivel, {})
    perfil = [c for c in cfg.get("perfil", []) if c in entidades.columns]
    if not perfil or entidades.empty:
        return pd.DataFrame()

    d = entidades.copy()
    d[columna_id] = d[columna_id].astype(str)
    if entidad not in set(d[columna_id]):
        return pd.DataFrame()

    X = d[perfil].apply(pd.to_numeric, errors="coerce").astype(float)
    X = X.fillna(X.median())
    escala = X.apply(_mad_serie).replace(0, np.nan).fillna(1.0)
    Z = ((X - X.median()) / escala).to_numpy(dtype=float)

    i = d.index[d[columna_id] == entidad][0]
    pos = d.index.get_loc(i)
    dist = np.linalg.norm(Z - Z[pos], axis=1)
    d = d.assign(_distancia=dist).sort_values("_distancia")
    return d.head(k + 1).drop(columns=["_distancia"]).reset_index(drop=True)
