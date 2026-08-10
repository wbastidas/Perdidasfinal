"""Dominios, subtipos y contingencias: las reglas que gobiernan el formulario.

Esta es la pieza que reproduce el comportamiento del modelo de datos del SIG que
todo editor de ArcGIS da por sentado: **al cambiar el subtipo de un elemento,
cambian los dominios de ciertos campos**, sus valores por defecto y, a veces, qué
campos siquiera aplican.

No es una comodidad de interfaz. En un banco en delta abierto las fases posibles
son AB, BC o CA —nunca ABC, que es físicamente imposible con dos unidades—. Si el
formulario ofrece las siete combinaciones, tarde o temprano alguien elige ABC, y
ese dato entra al modelo: el flujo reparte la carga entre tres fases que no
existen, el desbalance calculado deja de tener sentido y la pérdida técnica de esa
zona queda mal para siempre. El dominio dependiente del subtipo no es una ayuda
al técnico, es lo que impide que un dato imposible sea capturable.

**Cuatro piezas:**

* ``Dominio`` — valores permitidos: codificados (código + descripción) o rango.
* ``Subtipo`` — una categoría del elemento que **redefine** dominios, valores por
  defecto y campos aplicables.
* ``Regla`` — contingencia general: el dominio de un campo depende del valor de
  *otro* campo cualquiera, no solo del subtipo.
* ``aplicar_subtipo`` — qué pasa con lo ya capturado cuando el subtipo cambia.

Ese último punto es el que decide si el mecanismo suma o resta. Un valor que deja
de ser válido no se puede conservar —sería exactamente el dato imposible que se
quería evitar— pero tampoco se puede borrar en silencio, porque el técnico lo
capturó en sitio y no volverá. Se resuelve por defecto del subtipo nuevo cuando lo
hay, y si no, se limpia **avisando y mostrando lo que había**.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------- #
# Dominios
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ValorCodificado:
    """Un valor de dominio: lo que se guarda y lo que se lee.

    Separarlos importa: la base comercial necesita el código `RB01`, pero nadie
    elige bien entre `RB01` y `RB02` en un teléfono a pleno sol. El técnico ve
    «Residencial baja tensión» y se guarda `RB01`.
    """

    codigo: str
    descripcion: str = ""

    def etiqueta(self) -> str:
        return self.descripcion or self.codigo


@dataclass(frozen=True)
class Dominio:
    """Valores permitidos de un campo.

    Dos formas, como en el SIG: **codificado** (una lista cerrada) y **rango**
    (mínimo y máximo, para magnitudes continuas). El rango existe porque un poste
    de 40 m no es un valor de una lista: es un error de tecleo, y lo único que
    hace falta es que no entre.
    """

    nombre: str
    tipo: str = "CODIFICADO"             # CODIFICADO | RANGO
    valores: tuple[ValorCodificado, ...] = ()
    minimo: float | None = None
    maximo: float | None = None
    descripcion: str = ""

    @classmethod
    def codificado(cls, nombre: str, valores, descripcion: str = "") -> "Dominio":
        """Acepta ``["A", "B"]`` y ``[("RB01", "Residencial"), …]`` indistintamente."""

        return cls(nombre=nombre, tipo="CODIFICADO",
                   valores=tuple(_a_valor(v) for v in valores),
                   descripcion=descripcion)

    @classmethod
    def rango(cls, nombre: str, minimo: float, maximo: float,
              descripcion: str = "") -> "Dominio":
        return cls(nombre=nombre, tipo="RANGO", minimo=minimo, maximo=maximo,
                   descripcion=descripcion)

    @property
    def codigos(self) -> list[str]:
        return [v.codigo for v in self.valores]

    def admite(self, valor: Any) -> bool:
        """¿Es este valor aceptable? Vacío siempre lo es: lo obligatorio se
        controla aparte, y confundirlos haría imposible dejar un campo sin
        llenar mientras se decide."""

        if valor is None or valor == "":
            return True
        if self.tipo == "RANGO":
            try:
                n = float(valor)
            except (TypeError, ValueError):
                return False
            return ((self.minimo is None or n >= self.minimo)
                    and (self.maximo is None or n <= self.maximo))
        return _clave(valor) in {_clave(c) for c in self.codigos}

    def descripcion_de(self, codigo: Any) -> str:
        objetivo = _clave(codigo)
        for v in self.valores:
            if _clave(v.codigo) == objetivo:
                return v.etiqueta()
        return str(codigo) if codigo is not None else ""

    def a_dict(self) -> dict:
        d: dict[str, Any] = {"nombre": self.nombre, "tipo": self.tipo}
        if self.descripcion:
            d["descripcion"] = self.descripcion
        if self.tipo == "RANGO":
            d["minimo"], d["maximo"] = self.minimo, self.maximo
        else:
            d["valores"] = [{"codigo": v.codigo, "descripcion": v.etiqueta()}
                            for v in self.valores]
        return d


def _clave(v: Any) -> str:
    """Normaliza para comparar códigos con lo que SQLite devuelve.

    Un campo de tensión declarado REAL vuelve como ``220.0``, y el dominio dice
    ``"220"``. Comparar los textos crudos rechazaría un dato perfectamente
    correcto en cada tramo de baja tensión del país — que fue exactamente lo que
    pasó la primera vez que esto corrió sobre la red de ejemplo.
    """

    s = str(v).strip()
    try:
        f = float(s)
    except ValueError:
        return s
    return str(int(f)) if f == int(f) else str(f)


def _a_valor(v) -> ValorCodificado:
    if isinstance(v, ValorCodificado):
        return v
    if isinstance(v, (tuple, list)) and len(v) == 2:
        return ValorCodificado(str(v[0]), str(v[1]))
    return ValorCodificado(str(v), "")


# --------------------------------------------------------------------------- #
# Subtipos
# --------------------------------------------------------------------------- #
@dataclass
class Subtipo:
    """Una categoría del elemento que redefine el comportamiento del formulario.

    Es el mismo concepto que el subtipo del SIG: no es «otro valor de un campo»,
    es otra variante del elemento, con sus propias reglas.
    """

    codigo: str
    etiqueta: str = ""
    descripcion: str = ""
    # Dominios que ESTE subtipo impone, por campo. Reemplazan al dominio base.
    dominios: dict[str, Dominio] = field(default_factory=dict)
    # Qué se rellena solo al elegir este subtipo. Ahorra toques y, sobre todo,
    # evita el valor que el técnico dejaría por omisión sin darse cuenta.
    defectos: dict[str, Any] = field(default_factory=dict)
    # Campos que este subtipo no usa: preguntar la sección de ducto en una red
    # aérea es pedir un dato que no existe, y alguien acabará inventándolo.
    ocultos: tuple[str, ...] = ()
    # Obligatorios propios del subtipo, más allá de los de la capa.
    obligatorios: tuple[str, ...] = ()

    def titulo(self) -> str:
        return self.etiqueta or self.codigo

    def a_dict(self) -> dict:
        return {
            "codigo": self.codigo,
            "etiqueta": self.titulo(),
            "descripcion": self.descripcion,
            "dominios": {k: v.a_dict() for k, v in self.dominios.items()},
            "defectos": self.defectos,
            "ocultos": list(self.ocultos),
            "obligatorios": list(self.obligatorios),
        }


@dataclass
class Regla:
    """Contingencia general: el dominio de un campo depende de **otro** campo.

    El subtipo cubre el caso habitual, pero no todos. En una acometida
    subterránea el calibre disponible depende del ducto, y el ducto no es el
    subtipo del elemento. Sin esto, esos casos vuelven a la lista completa y el
    formulario ofrece combinaciones que no se pueden instalar.
    """

    campo_condicion: str
    valor_condicion: str
    campo_afectado: str
    dominio: Dominio

    def aplica(self, atributos: dict) -> bool:
        actual = atributos.get(self.campo_condicion)
        return actual is not None and str(actual) == self.valor_condicion

    def a_dict(self) -> dict:
        return {
            "campo_condicion": self.campo_condicion,
            "valor_condicion": self.valor_condicion,
            "campo_afectado": self.campo_afectado,
            "dominio": self.dominio.a_dict(),
        }


# --------------------------------------------------------------------------- #
# Resolución: qué dominio rige aquí y ahora
# --------------------------------------------------------------------------- #
@dataclass
class CambioSubtipo:
    """Consecuencias de cambiar el subtipo de un elemento ya capturado."""

    atributos: dict[str, Any]
    # Campos cuyo valor no cabía en el subtipo nuevo y se resolvieron por defecto.
    ajustados: dict[str, tuple[Any, Any]] = field(default_factory=dict)
    # Campos que se quedaron sin valor: no había defecto al que caer.
    invalidados: dict[str, Any] = field(default_factory=dict)
    # Campos que este subtipo no usa y llevaban algo escrito.
    descartados: dict[str, Any] = field(default_factory=dict)

    @property
    def hubo_perdida(self) -> bool:
        return bool(self.invalidados or self.descartados)

    def resumen(self) -> str:
        """Lo que hay que mostrarle al técnico. En su idioma, no en el del modelo."""

        partes = []
        if self.ajustados:
            partes.append("; ".join(
                f"«{c}» pasó de {_txt(a)} a {_txt(b)}"
                for c, (a, b) in self.ajustados.items()))
        if self.invalidados:
            partes.append("hay que volver a elegir " + ", ".join(
                f"«{c}» (antes {_txt(v)})" for c, v in self.invalidados.items()))
        if self.descartados:
            partes.append("no aplican a este subtipo: " + ", ".join(
                f"«{c}» ({_txt(v)})" for c, v in self.descartados.items()))
        return ". ".join(partes)


def _txt(v: Any) -> str:
    return "vacío" if v is None or v == "" else str(v)


def dominio_efectivo(capa, campo: str, atributos: dict) -> Dominio | None:
    """El dominio que rige para ``campo`` dado lo que el elemento tiene ahora.

    Precedencia, de más específico a más general:

    1. Una **regla de contingencia** que se cumpla — es la condición más
       concreta que se puede expresar.
    2. El dominio del **subtipo** actual.
    3. El dominio **base** del campo.

    El orden importa: si el subtipo ganara a la contingencia, la regla más
    específica no serviría de nada y habría que duplicarla en cada subtipo.
    """

    for r in getattr(capa, "reglas", ()) or ():
        if r.campo_afectado == campo and r.aplica(atributos):
            return r.dominio

    sub = subtipo_actual(capa, atributos)
    if sub and campo in sub.dominios:
        return sub.dominios[campo]

    for f in capa.campos:
        if f.nombre == campo:
            return f.dominio_obj()
    return None


def subtipo_actual(capa, atributos: dict) -> Subtipo | None:
    """El subtipo del elemento, o ``None`` si todavía no tiene ninguno.

    **No se supone uno.** Buena parte de la red llega del SIG sin el campo
    poblado —el modelo de origen no siempre lo trae—, y asumir el primero
    aplicaría a esos elementos unas reglas que nadie eligió: un tramo de baja
    tensión heredaría el dominio de media y sus 220 V se rechazarían por
    inválidos. Sin subtipo rige el dominio **base**, que es el superconjunto
    correcto.
    """

    nombre = getattr(capa, "campo_subtipo", None)
    if not nombre:
        return None
    actual = atributos.get(nombre)
    if actual is None or str(actual).strip() == "":
        return None
    return next((s for s in capa.subtipos if str(actual) == s.codigo), None)


def subtipo_por_defecto(capa) -> Subtipo | None:
    """El subtipo que se propone al **crear** un elemento nuevo.

    Aquí sí hay que elegir uno: un formulario en blanco sin subtipo obliga al
    técnico a acertar el campo correcto antes de que el resto tenga sentido.
    """

    return capa.subtipos[0] if getattr(capa, "subtipos", None) else None


def campos_aplicables(capa, atributos: dict) -> list[str]:
    """Qué campos tiene sentido mostrar para el subtipo actual."""

    sub = subtipo_actual(capa, atributos)
    ocultos = set(sub.ocultos) if sub else set()
    return [f.nombre for f in capa.campos if f.nombre not in ocultos]


def es_obligatorio(capa, campo: str, atributos: dict) -> bool:
    sub = subtipo_actual(capa, atributos)
    if sub and campo in sub.obligatorios:
        return True
    for f in capa.campos:
        if f.nombre == campo:
            return f.obligatorio
    return False


def aplicar_subtipo(capa, atributos: dict, nuevo: str) -> CambioSubtipo:
    """Cambia el subtipo y arregla lo que deja de encajar.

    La regla, campo por campo:

    * si lo capturado sigue siendo válido → **se respeta**. El técnico lo vio en
      sitio; el sistema no tiene por qué opinar.
    * si no, y el subtipo nuevo trae un valor por defecto → se aplica y **se
      avisa**, que es distinto de cambiarlo por debajo.
    * si no hay defecto → se limpia y se pide volver a elegir, mostrando lo que
      había. Conservarlo sería dejar entrar justo el dato imposible que el
      subtipo existe para impedir.

    Nada de esto es silencioso a propósito: una corrección invisible en un
    teléfono es una corrección que el técnico descubre semanas después, cuando ya
    no puede verificar nada.
    """

    nombre = getattr(capa, "campo_subtipo", None)
    if not nombre:
        return CambioSubtipo(atributos=dict(atributos))

    salida = dict(atributos)
    salida[nombre] = nuevo
    destino = next((s for s in capa.subtipos if s.codigo == str(nuevo)), None)
    cambio = CambioSubtipo(atributos=salida)
    if destino is None:
        return cambio

    for campo, defecto in destino.defectos.items():
        # El defecto solo rellena lo vacío. Pisar un valor capturado en sitio
        # porque el subtipo «sugiere» otro es perder trabajo de campo.
        if salida.get(campo) in (None, ""):
            salida[campo] = defecto

    for f in capa.campos:
        if f.nombre in (nombre, "guid", "fid"):
            continue
        valor = salida.get(f.nombre)

        if f.nombre in destino.ocultos:
            if valor not in (None, ""):
                cambio.descartados[f.nombre] = valor
                salida[f.nombre] = None
            continue

        dom = dominio_efectivo(capa, f.nombre, salida)
        if dom is None or dom.admite(valor) or valor in (None, ""):
            continue

        if f.nombre in destino.defectos:
            nuevo_valor = destino.defectos[f.nombre]
            cambio.ajustados[f.nombre] = (valor, nuevo_valor)
            salida[f.nombre] = nuevo_valor
        else:
            cambio.invalidados[f.nombre] = valor
            salida[f.nombre] = None

    return cambio


@dataclass
class Incoherencia:
    capa: str
    guid: str
    campo: str
    valor: Any
    motivo: str

    def __str__(self) -> str:
        return (f"{self.capa}/{self.guid[:8]} · «{self.campo}» = "
                f"{_txt(self.valor)}: {self.motivo}")


def validar(capa, atributos: dict, *, guid: str = "") -> list[Incoherencia]:
    """Comprueba lo capturado contra los dominios que de verdad rigen.

    Se ejecuta también **en el servidor** al sincronizar, y no por desconfianza
    del técnico: un paquete puede venir de una versión anterior de la aplicación,
    de un dispositivo con el esquema viejo o de una edición hecha con otra
    herramienta sobre el mismo GeoPackage. Validar solo en el móvil sería validar
    donde no está la responsabilidad del dato.
    """

    fallos: list[Incoherencia] = []
    sub = subtipo_actual(capa, atributos)
    ocultos = set(sub.ocultos) if sub else set()
    id_ = guid or str(atributos.get("guid", ""))

    for f in capa.campos:
        if f.nombre in ocultos:
            continue
        valor = atributos.get(f.nombre)

        if es_obligatorio(capa, f.nombre, atributos) and valor in (None, ""):
            fallos.append(Incoherencia(capa.nombre, id_, f.nombre, valor,
                                       "es obligatorio y quedó vacío"))
            continue

        dom = dominio_efectivo(capa, f.nombre, atributos)
        if dom is None or dom.admite(valor):
            continue

        if dom.tipo == "RANGO":
            motivo = f"fuera del rango permitido ({dom.minimo}–{dom.maximo})"
        elif sub and f.nombre in sub.dominios:
            # El mensaje dice POR QUÉ no vale: sin nombrar el subtipo, el
            # supervisor ve «valor inválido» en un campo cuyo valor existe en el
            # catálogo, y lo toma por un error del sistema.
            motivo = (f"no es válido para el subtipo «{sub.titulo()}» "
                      f"(permitidos: {', '.join(dom.codigos)})")
        else:
            motivo = f"no está en el dominio «{dom.nombre}»"
        fallos.append(Incoherencia(capa.nombre, id_, f.nombre, valor, motivo))

    return fallos
