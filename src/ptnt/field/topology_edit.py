"""Edición topológica: snap y propagación de cambios entre elementos conectados.

El requisito operativo es simple de enunciar y difícil de implementar bien: *si
muevo un cliente, la red conectada se mueve con él*. Sin eso, un técnico que
corrige la posición de un medidor deja la acometida colgando en el aire, y el
modelo de red queda peor que antes de la visita.

**La conectividad no se deduce de la geometría.** Dos elementos pueden estar
dibujados uno encima del otro sin estar conectados eléctricamente, y estar
conectados sin tocarse (un tramo subterráneo, una acometida mal digitalizada).
Por eso la propagación se calcula sobre el **grafo explícito** de
``ptnt_conexion`` —construido desde ``CIRCUITSOURCEGUID`` /
``PARENTCIRCUITSOURCEGUID``— y la proximidad geométrica se usa solo para el
*snap* al capturar, nunca para decidir qué se arrastra.

**Cuatro reglas de propagación**, en orden de precedencia:

1. **PERTENECE_A** — mover un puesto de transformación mueve sus unidades. No es
   una decisión: las unidades *están* en el puesto, no tienen posición propia.
2. **ACOMETIDA** — mover un cliente arrastra el **extremo** de su acometida, no
   la línea entera. El otro extremo sigue anclado al poste, que no se movió.
3. **COMPARTE_VERTICE** — dos tramos que comparten un vértice lo siguen
   compartiendo. Es lo que evita que la red se "rompa" en el punto de unión.
4. **ALIMENTA** — la relación eléctrica aguas abajo **no** propaga geometría. Un
   transformador alimenta a cien clientes; moverlo no debe mover el barrio.

La cuarta regla es la que más se equivoca al implementar esto, y la que más caro
sale: una propagación por conectividad eléctrica sin límite arrastra el
alimentador entero con un solo arrastre de dedo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum


class TipoRelacion(str, Enum):
    """Relaciones del grafo, con su comportamiento ante un movimiento."""

    PERTENECE_A = "PERTENECE_A"          # unidad → puesto: se mueve completo
    ACOMETIDA = "ACOMETIDA"              # cliente → tramo: se mueve el extremo
    COMPARTE_VERTICE = "COMPARTE_VERTICE"  # tramo ↔ tramo: se mueve el vértice
    ALIMENTA = "ALIMENTA"                # eléctrica: NO propaga geometría

    @property
    def propaga_geometria(self) -> bool:
        return self is not TipoRelacion.ALIMENTA


@dataclass
class Elemento:
    """Un elemento editable, con su geometría en coordenadas proyectadas."""

    guid: str
    capa: str
    # Punto: [(x, y)]; línea: [(x1,y1), (x2,y2), …]
    coords: list[tuple[float, float]] = field(default_factory=list)
    atributos: dict = field(default_factory=dict)

    @property
    def es_punto(self) -> bool:
        return len(self.coords) == 1


@dataclass
class Conexion:
    guid_origen: str
    guid_destino: str
    tipo: TipoRelacion


@dataclass
class CambioGeometria:
    """Un desplazamiento aplicado a un elemento."""

    guid: str
    capa: str
    coords_antes: list[tuple[float, float]]
    coords_despues: list[tuple[float, float]]
    propagado_de: str | None = None
    motivo: str = ""


@dataclass
class ResultadoEdicion:
    cambios: list[CambioGeometria] = field(default_factory=list)
    advertencias: list[str] = field(default_factory=list)

    @property
    def n_propagados(self) -> int:
        return sum(1 for c in self.cambios if c.propagado_de)


class GrafoEdicion:
    """Grafo de conectividad con las operaciones de edición topológica."""

    def __init__(self, elementos: dict[str, Elemento],
                 conexiones: list[Conexion]):
        self.elementos = elementos
        self.conexiones = conexiones
        self._vecinos: dict[str, list[tuple[str, TipoRelacion]]] = {}
        for c in conexiones:
            self._vecinos.setdefault(c.guid_origen, []).append(
                (c.guid_destino, c.tipo))
            self._vecinos.setdefault(c.guid_destino, []).append(
                (c.guid_origen, c.tipo))

    # -- consulta ----------------------------------------------------------
    def vecinos(self, guid: str, tipos: set[TipoRelacion] | None = None
                ) -> list[tuple[str, TipoRelacion]]:
        v = self._vecinos.get(guid, [])
        return [x for x in v if tipos is None or x[1] in tipos]

    def relacionados(self, guid: str) -> list[dict]:
        """Elementos relacionados, para el panel "Relacionados" del móvil.

        Es lo que permite, parado sobre un puesto, ver y editar sus unidades sin
        buscarlas en otra pantalla — el modelo Puesto→Unidad hecho operativo.
        """

        salida = []
        for otro, tipo in self.vecinos(guid):
            el = self.elementos.get(otro)
            if el is None:
                continue
            salida.append({
                "guid": otro, "capa": el.capa, "relacion": tipo.value,
                "codigo": el.atributos.get("codigo") or el.atributos.get(
                    "cuenta_contrato", ""),
                "editable": True,
            })
        return sorted(salida, key=lambda d: (d["relacion"], d["capa"]))

    # -- snap --------------------------------------------------------------
    def snap(self, x: float, y: float, *, tolerancia_m: float = 3.0,
             capas: set[str] | None = None,
             excluir: str | None = None) -> tuple[float, float, str | None]:
        """Ajusta una coordenada al vértice más cercano dentro de la tolerancia.

        Se usa **al capturar**, para que un elemento nuevo quede exactamente sobre
        el poste y no a 40 cm. La tolerancia por defecto de 3 m es del orden del
        error del GPS de un teléfono: más estricta rechazaría ajustes válidos, más
        laxa pegaría el elemento al poste equivocado en una vereda angosta.

        Devuelve ``(x, y, guid_al_que_se_pegó)``.
        """

        mejor, mejor_d, mejor_guid = (x, y), tolerancia_m, None
        for guid, el in self.elementos.items():
            if guid == excluir or (capas and el.capa not in capas):
                continue
            for cx, cy in el.coords:
                d = math.hypot(cx - x, cy - y)
                if d < mejor_d:
                    mejor, mejor_d, mejor_guid = (cx, cy), d, guid
        return mejor[0], mejor[1], mejor_guid

    # -- movimiento con propagación ---------------------------------------
    def mover(
        self, guid: str, nueva_x: float, nueva_y: float, *,
        indice_vertice: int = 0,
        max_propagacion: int = 200,
    ) -> ResultadoEdicion:
        """Mueve un elemento y **arrastra lo que topológicamente debe seguirlo**.

        ``max_propagacion`` es un tope de seguridad: si el grafo viniera con una
        relación mal clasificada, sin tope un solo arrastre podría reescribir la
        geometría de miles de elementos y el técnico no tendría forma de
        deshacerlo en campo.
        """

        res = ResultadoEdicion()
        el = self.elementos.get(guid)
        if el is None:
            res.advertencias.append(f"Elemento {guid} no existe en el paquete.")
            return res
        if not el.coords:
            res.advertencias.append(f"Elemento {guid} no tiene geometría.")
            return res

        idx = min(max(indice_vertice, 0), len(el.coords) - 1)
        origen = el.coords[idx]
        dx, dy = nueva_x - origen[0], nueva_y - origen[1]
        if math.hypot(dx, dy) < 1e-9:
            return res

        antes = list(el.coords)
        if el.es_punto:
            el.coords = [(nueva_x, nueva_y)]
        else:
            el.coords = list(el.coords)
            el.coords[idx] = (nueva_x, nueva_y)
        res.cambios.append(CambioGeometria(
            guid=guid, capa=el.capa, coords_antes=antes,
            coords_despues=list(el.coords), motivo="Movimiento directo"))

        self._propagar(guid, origen, (nueva_x, nueva_y), dx, dy, res,
                       max_propagacion)
        return res

    def _propagar(
        self, guid_raiz: str, pos_antes: tuple[float, float],
        pos_despues: tuple[float, float], dx: float, dy: float,
        res: ResultadoEdicion, tope: int,
    ) -> None:
        """Arrastra los elementos conectados según la regla de cada relación."""

        visitados = {guid_raiz}
        cola: list[tuple[str, tuple[float, float], float, float]] = [
            (guid_raiz, pos_antes, dx, dy)]

        while cola:
            actual, p_antes, ddx, ddy = cola.pop(0)
            for otro, tipo in self.vecinos(actual):
                if otro in visitados or len(res.cambios) >= tope:
                    continue
                if not tipo.propaga_geometria:
                    continue
                el = self.elementos.get(otro)
                if el is None or not el.coords:
                    continue

                antes = list(el.coords)
                if tipo is TipoRelacion.PERTENECE_A:
                    # El elemento hijo no tiene posición propia: se mueve entero.
                    el.coords = [(x + ddx, y + ddy) for x, y in el.coords]
                    motivo = "Pertenece al elemento movido"
                    visitados.add(otro)
                    cola.append((otro, p_antes, ddx, ddy))
                else:
                    # ACOMETIDA / COMPARTE_VERTICE: solo el vértice que coincidía
                    # con la posición anterior. El resto de la línea no se toca.
                    nuevas, tocado = [], False
                    for x, y in el.coords:
                        if math.hypot(x - p_antes[0], y - p_antes[1]) < 0.5:
                            nuevas.append((x + ddx, y + ddy))
                            tocado = True
                        else:
                            nuevas.append((x, y))
                    if not tocado:
                        continue
                    el.coords = nuevas
                    motivo = ("Extremo de acometida"
                              if tipo is TipoRelacion.ACOMETIDA
                              else "Vértice compartido")
                    visitados.add(otro)

                res.cambios.append(CambioGeometria(
                    guid=otro, capa=el.capa, coords_antes=antes,
                    coords_despues=list(el.coords),
                    propagado_de=actual, motivo=motivo))

        if len(res.cambios) >= tope:
            res.advertencias.append(
                f"La propagación alcanzó el tope de {tope} elementos. Revise la "
                "clasificación de relaciones: una relación ALIMENTA marcada como "
                "COMPARTE_VERTICE arrastra el alimentador completo.")

    # -- eliminación -------------------------------------------------------
    def puede_eliminar(self, guid: str) -> tuple[bool, list[str]]:
        """Verifica si un elemento se puede eliminar sin dejar huérfanos.

        Eliminar un puesto de transformación con clientes colgando no es una
        operación válida: dejaría a esos clientes sin transformador y el balance
        de la zona quedaría sin sentido. El móvil debe **impedirlo** y explicar
        por qué, en vez de aceptarlo y que reviente en la sincronización.
        """

        el = self.elementos.get(guid)
        if el is None:
            return False, [f"El elemento {guid} no existe."]

        bloqueos: list[str] = []
        hijos = [o for o, t in self.vecinos(guid)
                 if t in (TipoRelacion.PERTENECE_A, TipoRelacion.ALIMENTA)]
        dependientes = [
            h for h in hijos
            if (e := self.elementos.get(h)) and e.capa in (
                "ptnt_cliente", "ptnt_luminaria", "ptnt_unidad_transformacion")
        ]
        if dependientes:
            por_capa: dict[str, int] = {}
            for h in dependientes:
                capa = self.elementos[h].capa
                por_capa[capa] = por_capa.get(capa, 0) + 1
            detalle = ", ".join(f"{n} de {c}" for c, n in sorted(por_capa.items()))
            bloqueos.append(
                f"Tiene {len(dependientes)} elemento(s) dependiente(s) ({detalle}). "
                "Reasígnelos o elimínelos primero.")
        return (not bloqueos), bloqueos

    def eliminar(self, guid: str, *, forzar: bool = False
                 ) -> tuple[bool, list[str]]:
        ok, bloqueos = self.puede_eliminar(guid)
        if not ok and not forzar:
            return False, bloqueos
        self.elementos.pop(guid, None)
        self.conexiones = [c for c in self.conexiones
                           if guid not in (c.guid_origen, c.guid_destino)]
        self._vecinos.pop(guid, None)
        for v in self._vecinos.values():
            v[:] = [x for x in v if x[0] != guid]
        return True, bloqueos if forzar else []

    # -- validación --------------------------------------------------------
    def validar(self) -> list[str]:
        """Problemas topológicos que la sincronización debe conocer.

        Se corre en el móvil antes de cerrar la orden: es mucho más barato que el
        técnico arregle un cliente sin transformador estando en el sitio, que
        descubrirlo tres días después en la oficina.
        """

        problemas: list[str] = []
        for guid, el in self.elementos.items():
            if el.capa == "ptnt_cliente":
                if not self.vecinos(guid, {TipoRelacion.ACOMETIDA,
                                           TipoRelacion.ALIMENTA}):
                    problemas.append(
                        f"Cliente {el.atributos.get('cuenta_contrato', guid)} sin "
                        "acometida ni transformador asignado.")
            if el.capa == "ptnt_unidad_transformacion":
                if not el.atributos.get("puesto_guid"):
                    problemas.append(
                        f"Unidad {el.atributos.get('codigo', guid)} sin puesto.")
            if el.capa == "ptnt_tramo" and len(el.coords) < 2:
                problemas.append(f"Tramo {guid} con menos de dos vértices.")
        return problemas


def construir_grafo(
    elementos: list[dict], conexiones: list[dict]
) -> GrafoEdicion:
    """Arma el grafo desde las filas del GeoPackage."""

    els = {}
    for e in elementos:
        els[str(e["guid"])] = Elemento(
            guid=str(e["guid"]), capa=e["capa"],
            coords=[tuple(map(float, c)) for c in e.get("coords", [])],
            atributos=e.get("atributos", {}),
        )
    cons = []
    for c in conexiones:
        try:
            tipo = TipoRelacion(c["tipo_relacion"])
        except ValueError:
            continue
        cons.append(Conexion(str(c["guid_origen"]), str(c["guid_destino"]), tipo))
    return GrafoEdicion(els, cons)
