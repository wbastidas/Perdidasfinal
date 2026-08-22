"""Quién puede ver y analizar qué: alcance por unidad de negocio.

La empresa tiene varias unidades de negocio y una matriz. Un analista de
Guayaquil no tiene por qué ver el padrón de Manabí —son clientes con nombre,
dirección y una sospecha de hurto encima—, y la matriz sí necesita verlas todas
y poder analizar la que quiera.

**Tres decisiones que gobiernan el módulo.**

1. **El alcance se aplica en la capa de datos, no en la interfaz.** Ocultar un
   botón no es control de acceso: la misma consulta por línea de comandos, o un
   `GET` a la API, devolvería los datos igual. Aquí el filtro va donde se leen
   las filas.

2. **Falla cerrado.** Un usuario sin unidad asignada **no ve nada**, no lo ve
   todo. Es la diferencia entre un alta a medias que se nota el primer día y una
   fuga de datos que nadie descubre. Lo mismo con un alimentador que no está en
   el catálogo organizacional: si no se puede demostrar de quién es, no se
   entrega.

3. **La matriz elige, no acumula.** Ver todas las unidades no significa
   analizarlas todas a la vez: se elige explícitamente cuál, y ese alcance queda
   registrado en el escenario. Un consolidado nacional que nadie pidió es la
   forma más rápida de que un número mal leído llegue a una reunión.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


class AlcanceError(Exception):
    """El usuario no tiene permiso sobre lo que pidió."""


@dataclass(frozen=True)
class Alcance:
    """Qué unidades de negocio puede ver y analizar un usuario.

    ``matriz=True`` es la oficina central: ve todas. En cualquier otro caso solo
    ve las que tenga asignadas, y si no tiene ninguna, no ve nada.
    """

    usuario: str
    unidades: frozenset[str] = frozenset()
    matriz: bool = False

    @classmethod
    def desde_usuario(cls, usuario) -> "Alcance":
        """Construye el alcance desde un ``User`` del almacén."""

        return cls(
            usuario=getattr(usuario, "username", str(usuario)),
            unidades=frozenset(getattr(usuario, "unidades", None) or ()),
            matriz=bool(getattr(usuario, "matriz", False)),
        )

    @property
    def ve_todo(self) -> bool:
        return self.matriz

    @property
    def sin_alcance(self) -> bool:
        """Usuario al que nadie asignó unidad: no puede ver nada todavía."""

        return not self.matriz and not self.unidades

    def puede_ver(self, unidad: str | None) -> bool:
        if self.matriz:
            return True
        if not unidad:
            # Un dato sin unidad no se puede atribuir a nadie. Entregarlo «por si
            # acaso» es exactamente cómo se filtra el padrón de otra unidad.
            return False
        return str(unidad) in self.unidades

    def exigir(self, unidad: str | None, que: str = "esa información") -> None:
        """Lanza :class:`AlcanceError` con un mensaje que se pueda leer."""

        if self.puede_ver(unidad):
            return
        if self.sin_alcance:
            raise AlcanceError(
                f"El usuario '{self.usuario}' no tiene ninguna unidad de negocio "
                "asignada, así que no puede ver ningún dato. Un administrador "
                "debe asignársela: ptnt usuario-unidad "
                f"{self.usuario} --unidad CNEL-GYE")
        if not unidad:
            raise AlcanceError(
                f"No se pudo determinar a qué unidad de negocio pertenece {que}. "
                "Sin esa atribución no se entrega: revise el catálogo "
                "organizacional (ptnt consolidar --jerarquia …).")
        raise AlcanceError(
            f"'{self.usuario}' pertenece a {', '.join(sorted(self.unidades))} y "
            f"{que} es de '{unidad}'. Fuera de su alcance.")

    def descripcion(self) -> str:
        if self.matriz:
            return "matriz — ve todas las unidades de negocio"
        if not self.unidades:
            return "sin unidad asignada — no ve ningún dato"
        return "unidad(es): " + ", ".join(sorted(self.unidades))

    # -- filtros sobre datos ------------------------------------------------
    def filtrar(self, df: pd.DataFrame, *,
                col_unidad: str = "unidad_negocio") -> pd.DataFrame:
        """Deja solo las filas que este usuario puede ver.

        Si la columna no existe, **se devuelve vacío** en vez del total: un
        conjunto sin la unidad no es un conjunto «de todos», es uno que no se
        puede filtrar, y devolverlo entero sería entregar lo que no corresponde.
        """

        if df is None or df.empty or self.matriz:
            return df
        if col_unidad not in df.columns:
            return df.iloc[0:0]
        return df[df[col_unidad].astype(str).isin(self.unidades)]

    def unidades_visibles(self, todas: list[str]) -> list[str]:
        if self.matriz:
            return sorted(set(todas))
        return sorted(set(todas) & self.unidades)


# --------------------------------------------------------------------------- #
# Resolución contra el catálogo organizacional
# --------------------------------------------------------------------------- #
@dataclass
class ResolucionEntidad:
    """A qué unidad pertenece un alimentador o una subestación."""

    entidad: str
    nivel: str                    # ALIMENTADOR | SUBESTACION | UNIDAD_NEGOCIO
    unidad_negocio: str = ""
    subestacion: str = ""
    alimentadores: list[str] = field(default_factory=list)
    encontrada: bool = False


def resolver_entidad(jerarquia, entidad: str, nivel: str) -> ResolucionEntidad:
    """Ubica una entidad en el catálogo organizacional.

    Devuelve ``encontrada=False`` si no está, y el llamador decide: para un
    usuario de unidad eso significa denegar —no se puede demostrar que sea
    suya—, y para la matriz, avisar de que el catálogo está incompleto.
    """

    nivel = nivel.upper()
    res = ResolucionEntidad(entidad=entidad, nivel=nivel)
    alims = getattr(jerarquia, "alimentadores", {}) or {}

    if nivel == "ALIMENTADOR":
        a = alims.get(entidad)
        if a is not None:
            res.unidad_negocio = getattr(a, "unidad_negocio", "")
            res.subestacion = getattr(a, "subestacion", "")
            res.alimentadores = [entidad]
            res.encontrada = True
        return res

    if nivel == "SUBESTACION":
        hijos = [c for c, a in alims.items()
                 if str(getattr(a, "subestacion", "")) == entidad]
        if hijos:
            primero = alims[hijos[0]]
            res.unidad_negocio = getattr(primero, "unidad_negocio", "")
            res.subestacion = entidad
            res.alimentadores = sorted(hijos)
            res.encontrada = True
            # Una subestación repartida entre dos unidades es un error del
            # catálogo, no un caso a resolver en silencio: se marca sin unidad
            # para que `exigir` lo rechace y alguien lo corrija.
            unidades = {str(getattr(alims[c], "unidad_negocio", "")) for c in hijos}
            if len(unidades) > 1:
                res.unidad_negocio = ""
        return res

    if nivel == "UNIDAD_NEGOCIO":
        hijos = [c for c, a in alims.items()
                 if str(getattr(a, "unidad_negocio", "")) == entidad]
        if hijos:
            res.unidad_negocio = entidad
            res.alimentadores = sorted(hijos)
            res.encontrada = True
        return res

    raise AlcanceError(
        f"Nivel desconocido: '{nivel}'. Use ALIMENTADOR, SUBESTACION o "
        "UNIDAD_NEGOCIO.")


def exigir_entidad(alcance: Alcance, jerarquia, entidad: str,
                   nivel: str) -> ResolucionEntidad:
    """Resuelve la entidad y comprueba que el usuario pueda trabajar con ella.

    Es el punto único por el que pasan los escenarios: si alguien añade una vía
    nueva para abrir un alimentador, tiene que pasar por aquí o el control no
    existe.
    """

    res = resolver_entidad(jerarquia, entidad, nivel)

    if not res.encontrada:
        if alcance.matriz:
            # La matriz puede trabajar con lo que no está catalogado —suele ser
            # justo lo que hay que arreglar—, pero enterada de que lo está.
            res.unidad_negocio = res.unidad_negocio or ""
            return res
        raise AlcanceError(
            f"'{entidad}' no está en el catálogo organizacional, así que no se "
            f"puede determinar si pertenece a {', '.join(sorted(alcance.unidades)) or 'su unidad'}. "
            "No se entrega. Cárguelo con el catálogo de jerarquía o pida a "
            "matriz que lo revise.")

    alcance.exigir(res.unidad_negocio, f"el {nivel.lower()} '{entidad}'")
    return res
