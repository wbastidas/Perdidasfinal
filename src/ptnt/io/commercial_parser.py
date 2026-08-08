"""Parser del archivo comercial de consumos (§6.4 de la especificación).

Trampas de formato que este parser maneja explícitamente:

* El mismo carácter ``.`` significa cosas opuestas según la columna:
    - En ``KWH_*`` es **separador de miles**:  ``"1.302"`` → 1302,0
    - En coordenadas es **separador decimal**: ``"621077.988000000"`` → 621077.988
  Por eso se usan conversores por columna, nunca un ``thousands=``/``decimal=`` global.

* La orientación temporal de ``KWH_1..KWH_36`` es ambigua por diseño. Se resuelve
  en configuración y se **verifica**: si la orientación configurada no es la que
  mejor correlaciona con ``CLIULTCONM`` (último consumo-mes del padrón), se
  **aborta** la carga en lugar de corregir en silencio.

* Un error de separador de miles produce consumos 1000× menores de forma
  silenciosa; por eso se validan rangos plausibles por clase tarifaria.

Salida: DataFrame en formato **largo** (una fila por cuenta-mes), no ancho.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from ptnt.config.models import ComercialConfig


class CommercialParseError(Exception):
    """Error irrecuperable de parseo del archivo comercial."""


@dataclass
class ConsumoLargo:
    """Resultado del parseo comercial."""

    consumo: pd.DataFrame  # columnas: contract_account, period, kwh, is_zero, ...
    clientes: pd.DataFrame  # una fila por cuenta: division, nombre, tarifa, x, y, grupo
    meses: list[date]
    n_cuentas: int
    advertencias: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Conversores por columna
# --------------------------------------------------------------------------- #
def parse_kwh_value(raw: str, miles: str = ".", decimal: str = ",") -> float | None:
    """Convierte un valor de kWh según §6.4.

    ``"1.302"`` → 1302,0 ; ``"963"`` → 963,0 ; ``"0"`` → 0,0 ; vacío → None.
    El punto se trata como separador de miles y la coma como decimal.
    """

    if raw is None:
        return None
    s = str(raw).strip()
    if s == "" or s.lower() in {"nan", "none", "null"}:
        return None
    # Elimina separador de miles y normaliza el decimal a punto
    if miles:
        s = s.replace(miles, "")
    if decimal and decimal != ".":
        s = s.replace(decimal, ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_coord_value(raw: str, decimal: str = ".") -> float | None:
    """Convierte una coordenada UTM: el punto es separador decimal."""

    if raw is None:
        return None
    s = str(raw).strip()
    if s == "":
        return None
    if decimal and decimal != ".":
        s = s.replace(decimal, ".")
    try:
        return float(s)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Meses / orientación temporal
# --------------------------------------------------------------------------- #
def _month_first(d: date) -> date:
    return date(d.year, d.month, 1)


def _shift_month(d: date, delta: int) -> date:
    m0 = d.year * 12 + (d.month - 1) + delta
    return date(m0 // 12, m0 % 12 + 1, 1)


def build_month_axis(mes_final: str, n_meses: int, orden: str) -> list[date]:
    """Construye las fechas de los ``n`` meses.

    ``mes_final`` corresponde a la última columna ``KWH_n`` (el mes más reciente).
    Devuelve las fechas en el orden en que aparecen las columnas ``KWH_1..KWH_n``.
    """

    try:
        y, m, _d = (int(x) for x in mes_final.split("-"))
        fin = date(y, m, 1)
    except (ValueError, TypeError) as exc:
        raise CommercialParseError(
            f"comercial.mes_final inválido: '{mes_final}' (esperado YYYY-MM-DD)"
        ) from exc
    # cronológico ascendente (más antiguo -> más reciente)
    cronologico = [_shift_month(fin, -(n_meses - 1) + i) for i in range(n_meses)]
    if orden == "antiguo_primero":
        return cronologico
    if orden == "reciente_primero":
        return list(reversed(cronologico))
    raise CommercialParseError(f"comercial.orden_meses inválido: '{orden}'")


def verificar_orientacion(
    kwh_matrix: np.ndarray, meses: list[date], ultimo_consumo: np.ndarray | None
) -> tuple[bool, str]:
    """Verifica que la orientación temporal configurada sea correcta.

    Estrategia: el valor del mes más reciente (según la orientación configurada)
    debe correlacionar mejor con ``CLIULTCONM`` (último consumo del padrón) que el
    del mes más antiguo. Si no hay ``CLIULTCONM``, no se puede verificar y se
    acepta con advertencia.

    Devuelve ``(ok, mensaje)``.
    """

    if ultimo_consumo is None:
        return True, "Sin CLIULTCONM: orientación no verificable, se acepta la configurada."

    # Índice del mes más reciente y del más antiguo en el eje dado
    idx_reciente = int(np.argmax(meses))
    idx_antiguo = int(np.argmin(meses))

    col_reciente = kwh_matrix[:, idx_reciente]
    col_antiguo = kwh_matrix[:, idx_antiguo]

    mask = (
        np.isfinite(col_reciente)
        & np.isfinite(col_antiguo)
        & np.isfinite(ultimo_consumo)
        & (ultimo_consumo > 0)
    )
    if mask.sum() < 30:
        return True, "Muestra insuficiente para verificar orientación; se acepta."

    def _corr(a: np.ndarray, b: np.ndarray) -> float:
        if np.std(a) == 0 or np.std(b) == 0:
            return 0.0
        return float(np.corrcoef(a, b)[0, 1])

    corr_reciente = _corr(col_reciente[mask], ultimo_consumo[mask])
    corr_antiguo = _corr(col_antiguo[mask], ultimo_consumo[mask])

    if corr_reciente + 1e-9 >= corr_antiguo:
        return True, (
            f"Orientación verificada (corr reciente={corr_reciente:.3f} ≥ "
            f"antiguo={corr_antiguo:.3f})."
        )
    return False, (
        f"Orientación temporal INVERTIDA: el mes marcado como reciente correlaciona "
        f"peor con CLIULTCONM (reciente={corr_reciente:.3f} < antiguo={corr_antiguo:.3f}). "
        "Revise comercial.orden_meses. Se aborta para no corromper el análisis."
    )


# --------------------------------------------------------------------------- #
# Parser principal
# --------------------------------------------------------------------------- #
_KWH_RE = re.compile(r"^(.*?)(\d+)$")


def parse_commercial_csv(
    ruta: str, cfg: ComercialConfig
) -> ConsumoLargo:
    """Parsea el CSV comercial completo aplicando §6.4 íntegro."""

    col = cfg.columnas
    try:
        raw = pd.read_csv(
            ruta,
            sep=cfg.separador,
            encoding=cfg.encoding,
            dtype=str,
            keep_default_na=False,
        )
    except (OSError, UnicodeDecodeError) as exc:
        raise CommercialParseError(f"No se pudo leer '{ruta}': {exc}") from exc
    except pd.errors.ParserError as exc:
        raise CommercialParseError(f"CSV malformado '{ruta}': {exc}") from exc

    # Identificar columnas KWH_n en orden numérico
    kwh_cols: list[tuple[int, str]] = []
    for c in raw.columns:
        if c.startswith(col.prefijo_kwh):
            suf = c[len(col.prefijo_kwh):]
            if suf.isdigit():
                kwh_cols.append((int(suf), c))
    if not kwh_cols:
        raise CommercialParseError(
            f"No se encontraron columnas con prefijo '{col.prefijo_kwh}' en '{ruta}'"
        )
    kwh_cols.sort(key=lambda t: t[0])
    n_meses = len(kwh_cols)
    orden_cols = [c for _, c in kwh_cols]

    # Validar columnas obligatorias
    obligatorias = [col.division, col.cuenta_contrato, col.tarifa]
    faltan = [c for c in obligatorias if c not in raw.columns]
    if faltan:
        raise CommercialParseError(f"Faltan columnas obligatorias {faltan} en '{ruta}'")

    advertencias: list[str] = []

    # Matriz de kWh convertida
    miles = cfg.parseo_columnas.kwh.separador_miles or ""
    dec_kwh = cfg.parseo_columnas.kwh.separador_decimal or ","
    kwh_matrix = np.empty((len(raw), n_meses), dtype=float)
    for j, c in enumerate(orden_cols):
        vals = raw[c].map(lambda v: parse_kwh_value(v, miles, dec_kwh))
        kwh_matrix[:, j] = vals.astype(float)

    # Eje temporal
    meses = build_month_axis(cfg.mes_final, n_meses, cfg.orden_meses)

    # Verificación de orientación temporal
    ultimo = None
    if col.ultimo_consumo_mes and col.ultimo_consumo_mes in raw.columns:
        ultimo = raw[col.ultimo_consumo_mes].map(
            lambda v: parse_kwh_value(v, miles, dec_kwh)
        ).astype(float).to_numpy()
    if cfg.verificar_orientacion:
        ok, msg = verificar_orientacion(kwh_matrix, meses, ultimo)
        if not ok:
            raise CommercialParseError(msg)
        advertencias.append(msg)

    # Validación de rangos plausibles por clase tarifaria (detecta error de miles)
    _validar_rangos(raw, kwh_matrix, cfg, advertencias)

    # Construir formato largo
    cuentas = raw[col.cuenta_contrato].to_numpy()
    n = len(raw)
    long_rows = {
        "contract_account": np.repeat(cuentas, n_meses),
        "period": np.tile([m.isoformat() for m in meses], n),
        "kwh": kwh_matrix.reshape(-1),
    }
    consumo = pd.DataFrame(long_rows)
    consumo["period"] = pd.to_datetime(consumo["period"]).dt.date
    consumo["is_zero"] = consumo["kwh"].fillna(-1) == 0
    consumo["is_negative_flag"] = consumo["kwh"].fillna(0) < 0
    # Anular negativos (hallazgo R32 se emite en la capa de calidad)
    consumo.loc[consumo["is_negative_flag"], "kwh"] = np.nan
    consumo["is_estimated"] = False

    # Tabla de clientes (una fila por cuenta)
    clientes = pd.DataFrame({"contract_account": cuentas})
    clientes["division"] = raw[col.division].to_numpy()
    clientes["customer_name"] = (
        raw[col.nombre].to_numpy() if col.nombre in raw.columns else ""
    )
    clientes["tariff_description"] = raw[col.tarifa].to_numpy()
    dec_coord = cfg.parseo_columnas.coordenadas.separador_decimal or "."
    if col.x in raw.columns:
        clientes["x"] = raw[col.x].map(lambda v: parse_coord_value(v, dec_coord))
    if col.y in raw.columns:
        clientes["y"] = raw[col.y].map(lambda v: parse_coord_value(v, dec_coord))
    if col.grupo_lectura and col.grupo_lectura in raw.columns:
        clientes["grupo_lectura"] = raw[col.grupo_lectura].to_numpy()
    else:
        clientes["grupo_lectura"] = np.nan
    clientes = clientes.drop_duplicates(subset="contract_account").reset_index(drop=True)

    return ConsumoLargo(
        consumo=consumo,
        clientes=clientes,
        meses=meses,
        n_cuentas=int(clientes.shape[0]),
        advertencias=advertencias,
    )


def _validar_rangos(
    raw: pd.DataFrame,
    kwh_matrix: np.ndarray,
    cfg: ComercialConfig,
    advertencias: list[str],
) -> None:
    """Verifica que el consumo total por cliente esté en rango plausible por clase.

    Un error de separador de miles produce consumos 1000× menores; esta
    validación lo detecta a nivel agregado (no aborta, pero advierte con métrica).
    """

    if not cfg.rangos_plausibles_kwh_mes:
        return
    tarifa = raw[cfg.columnas.tarifa].to_numpy()
    prom_mensual = np.nanmean(kwh_matrix, axis=1)
    for clase, rango in cfg.rangos_plausibles_kwh_mes.items():
        mask = tarifa == clase
        if mask.sum() == 0:
            continue
        vals = prom_mensual[mask]
        vals = vals[np.isfinite(vals)]
        if vals.size == 0:
            continue
        fuera = np.mean((vals < rango.min) | (vals > rango.max)) * 100
        if fuera > 5.0:
            advertencias.append(
                f"Clase '{clase}': {fuera:.1f}% de clientes con promedio mensual "
                f"fuera de [{rango.min}, {rango.max}] kWh. Posible error de "
                "separador de miles o de clase tarifaria."
            )
