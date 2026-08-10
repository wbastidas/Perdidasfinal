"""Control de admisión: cuántas peticiones se atienden a la vez, y qué pasa con el resto.

La API móvil no sirve JSON pequeños: entrega paquetes GeoPackage de decenas de
megabytes y recibe paquetes de retorno que hay que **abrir, validar y recorrer**.
Cada subida es CPU y memoria de verdad.

A las siete de la mañana salen todas las cuadrillas y a las cinco de la tarde
vuelven todas juntas. Sin límite, cuarenta subidas simultáneas abren cuarenta
GeoPackages a la vez y el servidor se queda sin memoria — justo en el momento en
que el trabajo del día todavía no está a salvo en ningún otro sitio.

Con límite, se atienden N y **el resto espera en cola**. Tres decisiones:

1. **La cola tiene tope.** Una cola infinita no es amabilidad: es un timeout de
   dos minutos en el teléfono del técnico y un servidor que acumula peticiones
   que ya nadie está esperando. Al llenarse se responde **503 con `Retry-After`**,
   que la app entiende y reintenta sola.

2. **La espera tiene tope.** Mejor decir «vuelva en 30 segundos» que dejar al
   técnico mirando una barra de progreso diez minutos.

3. **Descargas y subidas tienen porteros distintos.** Una descarga es leer un
   archivo; una subida es abrir SQLite y recorrer el diario. Mezclarlas en el
   mismo límite haría que diez descargas baratas bloquearan una subida, que es la
   operación que **no se puede perder**.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field


class ServicioSaturado(Exception):
    """No hay capacidad ahora. Trae cuántos segundos conviene esperar."""

    def __init__(self, mensaje: str, reintentar_en_s: int = 15):
        super().__init__(mensaje)
        self.reintentar_en_s = reintentar_en_s


@dataclass
class MetricasPortero:
    atendidas: int = 0
    rechazadas: int = 0
    espera_total_s: float = 0.0
    espera_maxima_s: float = 0.0
    pico_en_curso: int = 0
    pico_en_cola: int = 0

    def resumen(self, en_curso: int, en_cola: int, limite: int) -> dict:
        return {
            "limite": limite,
            "en_curso": en_curso,
            "en_cola": en_cola,
            "atendidas": self.atendidas,
            "rechazadas": self.rechazadas,
            "espera_media_s": round(
                self.espera_total_s / self.atendidas, 3) if self.atendidas else 0.0,
            "espera_maxima_s": round(self.espera_maxima_s, 3),
            "pico_en_curso": self.pico_en_curso,
            "pico_en_cola": self.pico_en_cola,
        }


class Portero:
    """Deja pasar ``limite`` a la vez; encola hasta ``max_en_cola``; rechaza el resto."""

    def __init__(self, nombre: str, *, limite: int, max_en_cola: int = 32,
                 espera_maxima_s: float = 30.0):
        self.nombre = nombre
        self.limite = max(1, limite)
        self.max_en_cola = max(0, max_en_cola)
        self.espera_maxima_s = espera_maxima_s

        self._cond = threading.Condition()
        self._en_curso = 0
        self._en_cola = 0
        self.metricas = MetricasPortero()

    @property
    def en_curso(self) -> int:
        with self._cond:
            return self._en_curso

    @property
    def en_cola(self) -> int:
        with self._cond:
            return self._en_cola

    @contextmanager
    def turno(self, etiqueta: str = ""):
        """Espera turno y lo libera al salir, pase lo que pase dentro.

        Si el bloque revienta, el turno se libera igual: un error procesando un
        paquete no puede consumir una plaza para siempre y estrangular el
        servicio hasta el siguiente reinicio.
        """

        espera = self._entrar(etiqueta)
        try:
            yield espera
        finally:
            self._salir()

    def _entrar(self, etiqueta: str) -> float:
        inicio = time.monotonic()
        with self._cond:
            if self._en_curso < self.limite:
                self._en_curso += 1
                self._registrar_pico()
                self.metricas.atendidas += 1
                return 0.0

            if self._en_cola >= self.max_en_cola:
                self.metricas.rechazadas += 1
                # Se sugiere una espera proporcional a la cola: si hay veinte
                # delante, volver en un segundo solo genera veinte rechazos más.
                sugerido = self._reintento_sugerido()
                raise ServicioSaturado(
                    f"{self.nombre}: {self._en_curso} en curso y "
                    f"{self._en_cola} en cola. Reintente en {sugerido} s.",
                    reintentar_en_s=sugerido)

            self._en_cola += 1
            self._registrar_pico()
            try:
                limite_t = inicio + self.espera_maxima_s
                while self._en_curso >= self.limite:
                    restante = limite_t - time.monotonic()
                    if restante <= 0:
                        self.metricas.rechazadas += 1
                        sugerido = self._reintento_sugerido()
                        raise ServicioSaturado(
                            f"{self.nombre}: no hubo turno en "
                            f"{self.espera_maxima_s:.0f} s. Reintente en "
                            f"{sugerido} s.", reintentar_en_s=sugerido)
                    self._cond.wait(timeout=restante)
                self._en_curso += 1
            finally:
                self._en_cola -= 1

            espera = time.monotonic() - inicio
            self.metricas.atendidas += 1
            self.metricas.espera_total_s += espera
            self.metricas.espera_maxima_s = max(
                self.metricas.espera_maxima_s, espera)
            self._registrar_pico()
            return espera

    def _salir(self) -> None:
        with self._cond:
            self._en_curso = max(0, self._en_curso - 1)
            self._cond.notify()

    def _registrar_pico(self) -> None:
        self.metricas.pico_en_curso = max(self.metricas.pico_en_curso,
                                          self._en_curso)
        self.metricas.pico_en_cola = max(self.metricas.pico_en_cola,
                                         self._en_cola)

    def _reintento_sugerido(self) -> int:
        """Escalonar los reintentos evita que todos vuelvan en el mismo segundo.

        Sin esto, cuarenta teléfonos rechazados a la vez reintentan a la vez, y la
        saturación se repite en bucle en vez de resolverse.
        """

        base = max(5, int(self.espera_maxima_s / 2))
        return int(base + min(self._en_cola, 20) * 2)

    def resumen(self) -> dict:
        with self._cond:
            return self.metricas.resumen(self._en_curso, self._en_cola,
                                         self.limite)


@dataclass
class PorterosServicio:
    """Los porteros de la API móvil, con sus límites ya separados."""

    descargas: Portero
    subidas: Portero
    consultas: Portero
    extra: dict[str, Portero] = field(default_factory=dict)

    @classmethod
    def desde_config(cls, cfg) -> "PorterosServicio":
        return cls(
            descargas=Portero("descargas", limite=cfg.descargas_simultaneas,
                              max_en_cola=cfg.max_en_cola_api,
                              espera_maxima_s=cfg.espera_maxima_s),
            # Menos subidas que descargas: abrir y recorrer un GeoPackage cuesta
            # mucho más que servir un archivo ya construido.
            subidas=Portero("subidas", limite=cfg.subidas_simultaneas,
                            max_en_cola=cfg.max_en_cola_api,
                            espera_maxima_s=cfg.espera_maxima_s),
            # Las consultas son baratas y son las que el técnico hace primero:
            # si estas se saturan, la app parece caída aunque el resto funcione.
            consultas=Portero("consultas",
                              limite=max(8, cfg.descargas_simultaneas * 4),
                              max_en_cola=cfg.max_en_cola_api * 2,
                              espera_maxima_s=max(5.0, cfg.espera_maxima_s / 3)),
        )

    def resumen(self) -> dict:
        return {
            "descargas": self.descargas.resumen(),
            "subidas": self.subidas.resumen(),
            "consultas": self.consultas.resumen(),
            **{k: v.resumen() for k, v in self.extra.items()},
        }
