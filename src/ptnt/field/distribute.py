"""Reparto de órdenes entre varias cuadrillas.

Asignar trabajo a un equipo no es partir la lista en partes iguales. Dos cosas
compiten y hay que resolverlas juntas:

* **Carga pareja.** Si un técnico sale con 18 000 kWh en juego y otro con 3 000,
  el primero no termina y el segundo vuelve a media tarde. La medida de carga no
  es el número de órdenes: una orden de ruta comercial con 100 clientes no cuesta
  lo mismo que un sector con 14.

* **Coherencia geográfica.** Una jornada repartida por sorteo manda a la misma
  cuadrilla a dos extremos de la ciudad. El tiempo de traslado se come las
  visitas, y las visitas son lo único que recupera energía.

El reparto atiende las dos a la vez: cada orden va a la cuadrilla que minimiza
``distancia_normalizada + β · carga_relativa``. Con β alto manda el equilibrio;
con β bajo manda la cercanía. El valor por defecto (1.0) los pesa igual, y el
resultado se reporta con **ambas** métricas —desbalance y dispersión— para que la
decisión sea visible y no un número que salió de una caja negra.

Sin coordenadas utilizables el problema se reduce a repartir pesos, y se resuelve
con LPT (*longest processing time first*): ordenar de mayor a menor y dar cada
orden a quien va más liviano. Es el clásico, y su error está acotado por
4/3 − 1/(3m) respecto del óptimo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Qué columna mide el esfuerzo de una orden, según el criterio pedido.
_COLUMNA_PESO: dict[str, str] = {
    "kwh": "recuperable_kwh_mes",
    "clientes": "clientes_a_revisar",
    "visitas": "",           # todas las órdenes pesan lo mismo
}


@dataclass
class Reparto:
    """Resultado del reparto: qué le toca a cada técnico y qué quedó fuera."""

    por_usuario: dict[str, pd.DataFrame] = field(default_factory=dict)
    sin_asignar: pd.DataFrame = field(default_factory=pd.DataFrame)
    criterio: str = "kwh"
    motivo_sin_asignar: str = ""

    @property
    def total_asignadas(self) -> int:
        return int(sum(len(df) for df in self.por_usuario.values()))

    @property
    def desbalance_pct(self) -> float:
        """Diferencia entre la cuadrilla más y menos cargada, sobre la media.

        Cero es reparto perfecto. Por encima de ~25 % conviene revisar: suele
        significar que una orden enorme no cabe en ninguna partición pareja.
        """

        cargas = [self._peso(df) for df in self.por_usuario.values()]
        if not cargas:
            return 0.0
        media = float(np.mean(cargas))
        if media <= 0:
            return 0.0
        return float((max(cargas) - min(cargas)) / media * 100.0)

    def _peso(self, df: pd.DataFrame) -> float:
        col = _COLUMNA_PESO.get(self.criterio, "")
        if not col or col not in df.columns:
            return float(len(df))
        return float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum())

    def resumen(self) -> pd.DataFrame:
        """Una fila por técnico, con carga y dispersión del recorrido."""

        filas = []
        for usuario, df in self.por_usuario.items():
            filas.append({
                "usuario": usuario,
                "ordenes": len(df),
                "clientes": int(pd.to_numeric(
                    df.get("clientes_a_revisar", 0), errors="coerce"
                ).fillna(0).sum()) if len(df) else 0,
                "recuperable_kwh_mes": round(float(pd.to_numeric(
                    df.get("recuperable_kwh_mes", 0), errors="coerce"
                ).fillna(0).sum()), 1) if len(df) else 0.0,
                "peso": round(self._peso(df), 1),
                "dispersion_km": round(_dispersion_km(df), 2),
            })
        return pd.DataFrame(filas)

    def advertencias(self) -> list[str]:
        avisos = []
        if self.desbalance_pct > 25.0:
            avisos.append(
                f"Reparto desigual ({self.desbalance_pct:.0f} % entre la cuadrilla "
                f"más y menos cargada). Suele deberse a una orden mucho mayor que "
                f"el resto: revise si conviene partirla o asignarla aparte.")
        vacios = [u for u, df in self.por_usuario.items() if df.empty]
        if vacios:
            avisos.append(
                f"{len(vacios)} técnico(s) sin trabajo: {', '.join(vacios[:5])}. "
                f"Hay menos órdenes que cuadrillas o el tope por técnico es bajo.")
        if not self.sin_asignar.empty:
            avisos.append(
                f"{len(self.sin_asignar)} orden(es) quedaron sin asignar. "
                f"{self.motivo_sin_asignar}")
        return avisos


def repartir_ordenes(
    ordenes: pd.DataFrame,
    usuarios: list[str],
    *,
    criterio: str = "kwh",
    max_por_usuario: int | None = None,
    agrupar_geografico: bool = True,
    peso_balance: float = 1.0,
    iteraciones: int = 6,
) -> Reparto:
    """Reparte las órdenes entre los técnicos indicados.

    ``criterio`` decide qué se equilibra: ``kwh`` (energía recuperable, el
    defecto), ``clientes`` (esfuerzo de inspección) o ``visitas`` (solo el
    conteo). ``max_por_usuario`` es el tope de la jornada; lo que no cabe se
    devuelve en ``sin_asignar`` en vez de repartirse igual — una cuadrilla con más
    órdenes de las que puede hacer no las hace, las arrastra.

    ``peso_balance`` gradúa el compromiso: 0 reparte solo por cercanía (grupos
    compactos, cargas dispares); valores altos igualan cargas aunque el recorrido
    se alargue.
    """

    if criterio not in _COLUMNA_PESO:
        raise ValueError(
            f"Criterio '{criterio}' desconocido. Use uno de: "
            f"{', '.join(sorted(_COLUMNA_PESO))}.")
    usuarios = [u for u in dict.fromkeys(usuarios)]   # sin duplicados, en orden
    if not usuarios:
        raise ValueError("Se necesita al menos un técnico para repartir.")
    if ordenes is None or ordenes.empty:
        return Reparto(por_usuario={u: ordenes.iloc[0:0].copy() if ordenes is not None
                                    else pd.DataFrame() for u in usuarios},
                       sin_asignar=pd.DataFrame(), criterio=criterio)
    if "orden_trabajo" not in ordenes.columns:
        raise ValueError("Las órdenes deben traer la columna 'orden_trabajo'.")

    df = ordenes.reset_index(drop=True).copy()
    pesos = _pesos(df, criterio)

    # Tope de jornada: se atienden primero las órdenes de mayor peso, que es lo
    # que más energía recupera por día trabajado.
    cupo_total = len(df) if max_por_usuario is None else max_por_usuario * len(usuarios)
    motivo = ""
    if cupo_total < len(df):
        orden_prioridad = np.argsort(-pesos.to_numpy(), kind="stable")
        dentro = sorted(orden_prioridad[:cupo_total])
        fuera = sorted(orden_prioridad[cupo_total:])
        sin_asignar = df.iloc[fuera].copy()
        motivo = (f"El tope de {max_por_usuario} orden(es) por técnico agota la "
                  f"capacidad del equipo; se priorizaron las de mayor "
                  f"{_COLUMNA_PESO[criterio] or 'prioridad'}.")
        df = df.iloc[dentro].reset_index(drop=True)
        pesos = _pesos(df, criterio)
    else:
        sin_asignar = df.iloc[0:0].copy()

    coords = _coordenadas(df)
    if agrupar_geografico and coords is not None and len(usuarios) > 1:
        etiquetas = _reparto_geografico(
            coords, pesos.to_numpy(dtype=float), len(usuarios),
            max_por_usuario=max_por_usuario, peso_balance=peso_balance,
            iteraciones=iteraciones)
    else:
        etiquetas = _reparto_lpt(pesos.to_numpy(dtype=float), len(usuarios),
                                 max_por_usuario=max_por_usuario)

    por_usuario = {
        u: df.iloc[np.flatnonzero(etiquetas == i)].reset_index(drop=True)
        for i, u in enumerate(usuarios)
    }
    return Reparto(por_usuario=por_usuario, sin_asignar=sin_asignar,
                   criterio=criterio, motivo_sin_asignar=motivo)


def asignar_reparto(
    registro,
    reparto: Reparto,
    *,
    asignado_por: str = "",
    radio_m: float = 150.0,
) -> dict[str, list]:
    """Escribe el reparto en el registro, un técnico tras otro.

    Cada técnico entra en su propia transacción: si una asignación choca (alguien
    ya tenía esa orden), las demás cuadrillas no se quedan sin jornada por culpa
    de ese conflicto. El error se propaga para que el supervisor lo vea.
    """

    resultado: dict[str, list] = {}
    errores: list[str] = []
    for usuario, df in reparto.por_usuario.items():
        if df.empty:
            resultado[usuario] = []
            continue
        try:
            resultado[usuario] = registro.asignar(
                df, usuario, asignado_por=asignado_por, radio_m=radio_m)
        except (ValueError, KeyError) as exc:
            resultado[usuario] = []
            errores.append(f"{usuario}: {exc}")
    if errores:
        raise ValueError(
            "El reparto se aplicó parcialmente. Sin asignar:\n  - "
            + "\n  - ".join(errores))
    return resultado


# --------------------------------------------------------------------------- #
# Internos
# --------------------------------------------------------------------------- #
def _pesos(df: pd.DataFrame, criterio: str) -> pd.Series:
    col = _COLUMNA_PESO[criterio]
    if not col or col not in df.columns:
        return pd.Series(np.ones(len(df)), index=df.index, dtype=float)
    p = pd.to_numeric(df[col], errors="coerce").fillna(0.0).astype(float)
    # Peso mínimo positivo: una orden sin energía estimada sigue costando una
    # visita, y si pesara cero el reparto la trataría como gratis.
    piso = max(p[p > 0].min() * 0.1, 1e-6) if (p > 0).any() else 1.0
    return p.clip(lower=piso)


def _coordenadas(df: pd.DataFrame) -> np.ndarray | None:
    if not {"x", "y"}.issubset(df.columns):
        return None
    xy = df[["x", "y"]].apply(pd.to_numeric, errors="coerce")
    if xy.isna().any(axis=1).mean() > 0.10:
        # Con más de un 10 % de órdenes sin ubicar, agrupar por cercanía dice
        # más del hueco de datos que del territorio.
        return None
    xy = xy.fillna(xy.mean())
    return xy.to_numpy(dtype=float)


def _semillas(coords: np.ndarray, k: int) -> np.ndarray:
    """Semillas por punto más lejano: determinista y bien separada.

    k-means++ aleatorio daría repartos distintos en cada ejecución, y un
    supervisor que reparte dos veces la misma lista espera el mismo resultado.
    """

    idx = [int(np.argmax(coords[:, 0] + coords[:, 1]))]
    for _ in range(1, k):
        d = np.min([np.hypot(*(coords - coords[i]).T) for i in idx], axis=0)
        idx.append(int(np.argmax(d)))
    return coords[idx].copy()


def _reparto_geografico(coords: np.ndarray, pesos: np.ndarray, k: int, *,
                        max_por_usuario: int | None, peso_balance: float,
                        iteraciones: int) -> np.ndarray:
    """k-means con restricción de carga: compacto **y** parejo."""

    n = len(coords)
    centros = _semillas(coords, k)
    objetivo = float(pesos.sum()) / k
    escala = float(np.hypot(*(coords.max(axis=0) - coords.min(axis=0)))) or 1.0
    etiquetas = np.zeros(n, dtype=int)
    # De mayor a menor peso: las órdenes grandes se colocan cuando aún hay
    # holgura, que es cuando se las puede equilibrar.
    orden = np.argsort(-pesos, kind="stable")

    for it in range(max(1, iteraciones)):
        carga = np.zeros(k)
        cuenta = np.zeros(k, dtype=int)
        nuevas = np.full(n, -1, dtype=int)
        for i in orden:
            dist = np.hypot(*(centros - coords[i]).T) / escala
            penal = peso_balance * (carga / objetivo if objetivo > 0 else carga)
            costo = dist + penal
            if max_por_usuario is not None:
                costo = np.where(cuenta >= max_por_usuario, np.inf, costo)
                if not np.isfinite(costo).any():   # todos llenos: no debería pasar
                    costo = dist
            g = int(np.argmin(costo))
            nuevas[i] = g
            carga[g] += pesos[i]
            cuenta[g] += 1
        if it and np.array_equal(nuevas, etiquetas):
            break
        etiquetas = nuevas
        for g in range(k):
            m = etiquetas == g
            if m.any():
                centros[g] = coords[m].mean(axis=0)
    return etiquetas


def _reparto_lpt(pesos: np.ndarray, k: int, *,
                 max_por_usuario: int | None) -> np.ndarray:
    """Reparto por peso puro cuando no hay geografía en la que apoyarse."""

    carga = np.zeros(k)
    cuenta = np.zeros(k, dtype=int)
    etiquetas = np.zeros(len(pesos), dtype=int)
    for i in np.argsort(-pesos, kind="stable"):
        libres = np.arange(k)
        if max_por_usuario is not None:
            con_cupo = libres[cuenta < max_por_usuario]
            libres = con_cupo if len(con_cupo) else libres
        g = int(libres[np.argmin(carga[libres])])
        etiquetas[i] = g
        carga[g] += pesos[i]
        cuenta[g] += 1
    return etiquetas


def _dispersion_km(df: pd.DataFrame) -> float:
    """Distancia media de cada orden al centro de su grupo, en km.

    Es el indicador de cuánto va a manejar la cuadrilla. Un grupo compacto tiene
    dispersión de cientos de metros; uno repartido por la ciudad, de kilómetros.
    """

    if df.empty or not {"x", "y"}.issubset(df.columns):
        return 0.0
    xy = df[["x", "y"]].apply(pd.to_numeric, errors="coerce").dropna()
    if len(xy) < 2:
        return 0.0
    c = xy.mean().to_numpy()
    d = np.hypot(*(xy.to_numpy() - c).T)
    return float(np.mean(d) / 1000.0) if math.isfinite(float(np.mean(d))) else 0.0
