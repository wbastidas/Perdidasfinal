"""Modelos de configuración validados con pydantic v2.

Cada sección del YAML se mapea a un modelo. Los campos sin valor por defecto son
OBLIGATORIOS: si el YAML no los provee, pydantic lanza ``ValidationError`` y el
arranque falla nombrando el parámetro y su ruta (ver ``loader.load_config``).
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Strict(BaseModel):
    """Base con validación estricta: prohíbe claves desconocidas en el YAML.

    Una clave mal escrita en el YAML es un error de configuración, no algo que
    deba ignorarse en silencio; ``extra='forbid'`` lo convierte en fallo de
    arranque.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)


# ---------------------------------------------------------------------------
# Proyecto
# ---------------------------------------------------------------------------
class ProyectoConfig(_Strict):
    nombre: str
    version_config: str
    unidad_negocio: str = Field(..., min_length=1)  # OBLIGATORIO
    codigo_empresa: str
    zona_horaria: str = "America/Guayaquil"


# ---------------------------------------------------------------------------
# Fuentes de datos (multi-origen)
# ---------------------------------------------------------------------------
class TipoFuente(str, Enum):
    CSV = "csv"
    SQLSERVER = "sqlserver"
    POSTGRES = "postgres"
    ORACLE = "oracle"
    ORACLE_ARCSDE = "oracle_arcsde"   # Oracle 11gR2 + ArcSDE (ST_Geometry)
    MYSQL = "mysql"
    DUCKDB = "duckdb"
    PARQUET = "parquet"
    FGDB = "fgdb"                       # File Geodatabase de ArcGIS (OpenFileGDB)


class FuenteConfig(_Strict):
    """Definición de una base de origen.

    Las credenciales NUNCA se ponen en el YAML: se referencian por variable de
    entorno (``usuario_env`` / ``password_env``) y se resuelven en el conector.
    Ver ``ptnt.security.secrets``.
    """

    nombre: str
    tipo: TipoFuente
    # Conexión SQL (opcional según tipo)
    host: str | None = None
    puerto: int | None = None
    base_datos: str | None = None
    esquema: str | None = None
    driver: str | None = None  # p.ej. "ODBC Driver 18 for SQL Server"
    usuario_env: str | None = None   # nombre de la variable de entorno
    password_env: str | None = None  # nombre de la variable de entorno
    dsn_env: str | None = None       # alternativa: cadena completa por env
    opciones: dict[str, str] = Field(default_factory=dict)
    # Fuentes de archivo (csv/parquet/duckdb/fgdb)
    ruta: str | None = None
    # SSL/TLS
    requiere_ssl: bool = True
    # ArcSDE/ST_Geometry: columnas de geometría a envolver con SDE.ST_AsBinary
    st_geometry_cols: dict[str, str] = Field(default_factory=dict)  # tabla -> columna

    @model_validator(mode="after")
    def _validar_por_tipo(self) -> "FuenteConfig":
        archivo = {TipoFuente.CSV, TipoFuente.PARQUET, TipoFuente.DUCKDB, TipoFuente.FGDB}
        if self.tipo in archivo:
            if not self.ruta:
                raise ValueError(
                    f"fuente '{self.nombre}' de tipo {self.tipo.value} requiere 'ruta'"
                )
        else:
            faltantes = [
                c for c in ("host", "base_datos")
                if getattr(self, c) is None
            ]
            if faltantes:
                raise ValueError(
                    f"fuente '{self.nombre}' de tipo {self.tipo.value} "
                    f"requiere {faltantes}"
                )
        return self


# ---------------------------------------------------------------------------
# Ingesta comercial (§6.4)
# ---------------------------------------------------------------------------
class ParseoNumerico(_Strict):
    separador_miles: str | None = None
    separador_decimal: str | None = None


class ParseoColumnas(_Strict):
    kwh: ParseoNumerico
    coordenadas: ParseoNumerico


class ColumnasCSV(_Strict):
    division: str
    cuenta_contrato: str
    nombre: str
    tarifa: str
    x: str
    y: str
    prefijo_kwh: str
    # Campos opcionales del modelo de datos usados en el análisis
    grupo_lectura: str | None = None   # CLIRLSCOD — grupo de ruta de lectura
    ultimo_consumo_mes: str | None = None  # CLIULTCONM


class RangoPlausible(_Strict):
    min: float
    max: float


class ComercialConfig(_Strict):
    separador: str = ";"
    encoding: str = "latin-1"
    parseo_columnas: ParseoColumnas
    orden_meses: Literal["antiguo_primero", "reciente_primero"]
    verificar_orientacion: bool = True
    mes_final: str  # OBLIGATORIO — mes del último KWH_n (formato YYYY-MM-DD)
    columnas: ColumnasCSV
    rangos_plausibles_kwh_mes: dict[str, RangoPlausible] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Promedio multi-mes (mecanismo emphasizado por el usuario)
# ---------------------------------------------------------------------------
class MetodoPromedio(str, Enum):
    MEDIA = "media"                    # media aritmética simple
    MEDIA_RECORTADA = "media_recortada"  # descarta colas (robusto a outliers)
    MEDIANA = "mediana"                # mediana (muy robusto)
    MEDIA_PONDERADA = "media_ponderada"  # pondera meses recientes
    ESTACIONAL = "estacional"          # media por mes-calendario (12 factores)


class AveragingConfig(_Strict):
    """Parámetros del mecanismo de promedio de consumo sobre varios meses.

    El consumo llega como serie mensual (hasta 36 meses). Para el cálculo de
    demanda no se usa el último mes aislado (que es justamente el error del SIG),
    sino un promedio robusto de la ventana configurada.
    """

    metodo: MetodoPromedio = MetodoPromedio.MEDIA_RECORTADA
    ventana_meses: int = Field(12, ge=1, le=36)
    recorte_pct: float = Field(10.0, ge=0, le=40)  # para media_recortada
    half_life_meses: float = Field(6.0, gt=0)      # para media_ponderada
    # Excluir de la ventana meses en cero de clientes con servicio suspendido
    excluir_ceros_suspendidos: bool = True
    # Excluir lecturas estimadas del promedio (no se imputan en silencio)
    excluir_estimadas: bool = False
    # Mínimo de meses válidos requeridos para considerar el promedio confiable
    min_meses_validos: int = Field(3, ge=1)


# ---------------------------------------------------------------------------
# Clases tarifarias y modelado de carga (§6)
# ---------------------------------------------------------------------------
class ClaseTarifaria(_Strict):
    """Coeficientes por clase tarifaria.

    a, b: coeficientes de Velander (P_max = a*E + b*sqrt(E)).
    A, B: coincidencia FC(n) = A + B/sqrt(n).
    """

    a: float = Field(..., ge=0)
    b: float = Field(..., ge=0)
    A: float = Field(..., ge=0, le=1)
    B: float = Field(..., ge=0, le=1)
    cos_phi: float = Field(..., gt=0, le=1)
    factor_carga: float = Field(..., gt=0, le=1)
    k_perdidas: float = Field(..., ge=0, le=1)
    es_residencial: bool = False

    @model_validator(mode="after")
    def _coincidencia_valida(self) -> "ClaseTarifaria":
        # FC(1) = A + B debe ser 1.0 para que la diversificación sea consistente.
        if abs((self.A + self.B) - 1.0) > 1e-6:
            raise ValueError(
                f"A + B debe ser 1.0 (FC(1)=1); recibido A={self.A}, B={self.B}"
            )
        return self


class MetodoDemanda(str, Enum):
    VELANDER = "VELANDER"
    FACTOR_CARGA = "FACTOR_CARGA"


class LoadConfig(_Strict):
    metodo_demanda_maxima: MetodoDemanda = MetodoDemanda.VELANDER
    dias_periodo_por_defecto: int = Field(30, ge=1, le=366)
    prorrateo_calendario: bool = True
    coincidencia_minima: float = Field(0.15, gt=0, le=1)
    calibrar_contra_cabecera: bool = True
    # Tensiones nominales por configuración de fase (V)
    voltaje_ln: float = Field(127.0, gt=0)   # fase-neutro
    voltaje_ll: float = Field(220.0, gt=0)   # fase-fase
    clases: dict[str, ClaseTarifaria]

    @field_validator("clases")
    @classmethod
    def _no_vacio(cls, v: dict[str, ClaseTarifaria]) -> dict[str, ClaseTarifaria]:
        if not v:
            raise ValueError("carga.clases no puede estar vacío")
        return v


# ---------------------------------------------------------------------------
# Señales de PNT / hurto (§11.3)
# ---------------------------------------------------------------------------
class SignalsConfig(_Strict):
    # S1 — caída y recuperación
    s1_caida_min_pct: float = 40.0
    s1_meses_min_caida: int = 3
    s1_tolerancia_recuperacion_pct: float = 15.0
    # S3 — ruptura de nivel
    s3_cambio_min_pct: float = 30.0
    # S4 — cero con servicio activo
    s4_meses_min_cero: int = 3
    # S5 — divergencia grupo par
    s5_percentil: float = 5.0
    s5_min_pares: int = 5
    # S7 — planitud
    s7_cv_max: float = 0.05
    # S8 — dispersión intra-puesto
    s8_min_unidades: int = 3
    s8_cv_min: float = 0.60
    # S9 — déficit contra la base propia (clave en industrial/oficial, donde el
    # grupo par no es estadísticamente válido)
    s9_deficit_min: float = Field(0.35, ge=0.0, le=1.0)
    # No supervisado
    contaminacion: float = Field(0.05, gt=0, lt=0.5)
    usar_no_supervisado: bool = True
    # Campo de agrupamiento de grupo par usado como respaldo cuando el padrón no
    # viene segmentado. Con segmentación activa, el grupo par lo arma
    # `ptnt.segment.peers` (clase × tensión × estrato × CLIRLSCOD).
    campo_grupo_par: str = "grupo_lectura"


# ---------------------------------------------------------------------------
# Segmentación de clientes (§11.3)
# ---------------------------------------------------------------------------
class SegmentacionConfig(_Strict):
    """Ejes de segmentación del padrón para el análisis de PNT.

    Los cortes de estrato son configurables porque dependen de la distribución
    real de la distribuidora: los valores por defecto siguen los bloques de
    consumo residencial usados en el pliego tarifario ecuatoriano (incluido el
    límite de 130 kWh/mes de la Tarifa Dignidad en Costa/Oriente/Insular), pero
    deben ajustarse contra el histograma real del padrón antes de producción.
    """

    habilitada: bool = True
    columna_tarifa: str = "tariff_description"
    # Percentil de la historia propia que define el "nivel habitual" del cliente.
    # Alto a propósito: responde "de qué tamaño es este cliente", no "cuánto
    # consumió" — si se usara la media, un hurto prolongado bajaría el estrato y
    # el cliente terminaría comparado contra pares igual de deprimidos.
    percentil_consumo_base: float = Field(75.0, ge=50.0, le=100.0)
    # Mínimo de miembros para que un grupo par sea estadísticamente utilizable.
    min_pares: int = Field(8, ge=3)
    cortes_residencial_kwh: list[float] = Field(
        default_factory=lambda: [50, 100, 130, 200, 300, 500, 1000]
    )
    cortes_no_residencial_kwh: list[float] = Field(
        default_factory=lambda: [200, 1000, 5000, 20000, 100000]
    )
    # A partir de aquí un cliente se revisa de forma individual y no por su
    # posición relativa en el ranking: un error del 5 % en él vale más que el
    # 100 % de un residencial pequeño.
    umbral_gran_cliente_kwh_mes: float = Field(5000.0, gt=0)

    @field_validator("cortes_residencial_kwh", "cortes_no_residencial_kwh")
    @classmethod
    def _cortes_crecientes(cls, v: list[float]) -> list[float]:
        if not v:
            raise ValueError("La lista de cortes de estrato no puede estar vacía.")
        if any(b <= a for a, b in zip(v, v[1:])):
            raise ValueError(
                f"Los cortes de estrato deben ser estrictamente crecientes: {v}"
            )
        if v[0] <= 0:
            raise ValueError("El primer corte de estrato debe ser mayor que cero.")
        return v


# ---------------------------------------------------------------------------
# Seguridad
# ---------------------------------------------------------------------------
class SecurityConfig(_Strict):
    autenticacion_habilitada: bool = True
    # Nombre de la variable de entorno con la clave de firma de sesiones/JWT
    jwt_secret_env: str = "PTNT_JWT_SECRET"
    jwt_ttl_min: int = Field(60, ge=1)
    # Ruta al archivo de usuarios (hashes bcrypt), nunca contraseñas en claro
    ruta_usuarios: str = "config/usuarios.json"
    # Redes autorizadas para el visor (CIDR). Vacío = sin restricción de red.
    redes_permitidas: list[str] = Field(default_factory=list)
    # Máx. intentos de login antes de bloqueo temporal
    max_intentos_login: int = Field(5, ge=1)


# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------
class DashboardConfig(_Strict):
    puerto: int = 8501
    host: str = "127.0.0.1"
    cache_ttl_s: int = 600
    idioma: str = "es"
    formato_numero: str = "es_EC"


class WebviewerConfig(_Strict):
    puerto: int = 8080
    host: str = "127.0.0.1"
    titulo: str = "PTNT-BAL — Resultados"


class RutasConfig(_Strict):
    duckdb: str = "data/ptnt.duckdb"
    salidas: str = "outputs"
    bronze: str = "data/bronze"
    silver: str = "data/silver"
    gold: str = "data/gold"


# ---------------------------------------------------------------------------
# Migración de datos (origen → canónico)
# ---------------------------------------------------------------------------
class MigracionConfig(_Strict):
    """Declara de qué fuente y tablas se migra la red al modelo canónico.

    ``fuente`` es el nombre de una fuente definida en ``fuentes``. Las tablas y
    columnas son configurables para acomodar distintas bases de origen sin tocar
    el código; los valores por defecto corresponden al esquema canónico interno
    (el mismo que produce la exportación de una red).
    """

    fuente: str = "resultados_local"
    field_map: str = "config/field_map.yaml"
    tabla_segmentos: str = "silver.segments"
    tabla_puestos: str = "silver.transformer_sites"
    tabla_clientes: str = "silver.customer_nodes"
    tabla_luminarias: str = "silver.streetlights"
    tolerancia_longitud_pct: float = 30.0


# ---------------------------------------------------------------------------
# Catálogos (conductores y transformadores)
# ---------------------------------------------------------------------------
class CatalogosConfig(_Strict):
    conductores: str = "config/catalogo_conductores.yaml"
    transformadores: str = "config/catalogo_transformadores.yaml"


# ---------------------------------------------------------------------------
# Pérdidas técnicas (§8)
# ---------------------------------------------------------------------------
class MonteCarloConfig(_Strict):
    iteraciones_n1: int = Field(200, ge=10)
    iteraciones_n3: int = Field(1000, ge=10)
    semilla: int = 20260807
    p0_pk_pct: float = 15.0
    factor_carga_pct: float = 20.0
    conductor_atributo_pct: float = 10.0
    longitud_pct: float = 5.0


class PerdidasConfig(_Strict):
    # Factor de pérdidas F_p = k·Fc + (1-k)·Fc²
    k_por_tipo_alimentador: dict[str, float] = Field(
        default_factory=lambda: {"U": 0.30, "R": 0.20}
    )
    k_por_defecto: float = Field(0.25, ge=0, le=1)
    # Conductores
    temperatura_operacion_c: float = 50.0
    temperatura_referencia_c: float = 20.0
    alpha_aluminio: float = 0.00403
    alpha_cobre: float = 0.00393
    incluir_neutro: bool = True
    # Transformadores
    factor_desbalance_cobre: float = 1.02
    # ¡DEBE ser false! La pérdida en vacío no se afecta por el factor de pérdidas.
    aplicar_factor_perdidas_a_vacio: bool = False
    # Medidores: vatios por tipo
    watts_medidor: dict[str, float] = Field(
        default_factory=lambda: {
            "Electromecánico": 1.8,
            "Electrónico": 0.8,
            "Inteligente": 1.0,
            "_default": 1.0,
        }
    )
    monte_carlo: MonteCarloConfig = Field(default_factory=MonteCarloConfig)


# ---------------------------------------------------------------------------
# Alumbrado público (§9)
# ---------------------------------------------------------------------------
class AlumbradoConfig(_Strict):
    dias_mes_por_defecto: int = 30
    horas_min: float = 10.0
    horas_max: float = 13.0
    perdidas_auxiliares_w: dict[str, float] = Field(
        default_factory=lambda: {
            "LED": 4, "Sodio": 25, "Mercurio": 30,
            "Metal Halide": 28, "Inducción": 8, "_default": 20,
        }
    )
    # DEBE ser true — evita doble conteo de luminarias bajo medición
    excluir_si_bajo_medicion: bool = True
    sensibilidad_horas_pct: float = 20.0


# ---------------------------------------------------------------------------
# Balance energético (§10)
# ---------------------------------------------------------------------------
class ControlesBalance(_Strict):
    c02_pnt_maxima_pct: float = 60.0
    c04_perdidas_tecnicas_max_pct: float = 20.0
    c05_cobertura_clientes_pct: float = 10.0
    c06_cobertura_energia_min_pct: float = 70.0


class BalanceConfig(_Strict):
    tolerancia_cierre_pct: float = 0.5
    consumos_propios_pct_defecto: float = 0.1
    ens_por_defecto_kwh: float = 0.0
    controles: ControlesBalance = Field(default_factory=ControlesBalance)


# ---------------------------------------------------------------------------
# Cargabilidad (§11.7)
# ---------------------------------------------------------------------------
class CargabilidadConfig(_Strict):
    sobrecargado_critico: float = 1.20
    sobrecargado: float = 1.00
    alta_carga: float = 0.80
    adecuado_min: float = 0.30
    subutilizado_min: float = 0.15
    desbalance_umbral_pct: float = 10.0


# ---------------------------------------------------------------------------
# Flujo de potencia (§8.7)
# ---------------------------------------------------------------------------
class FlujoConfig(_Strict):
    tolerancia_convergencia: float = 1.0e-6
    max_iteraciones: int = Field(100, ge=1)


# ---------------------------------------------------------------------------
# Raíz
# ---------------------------------------------------------------------------
class AppConfig(_Strict):
    """Configuración raíz de la aplicación."""

    proyecto: ProyectoConfig
    rutas: RutasConfig = Field(default_factory=RutasConfig)
    fuentes: list[FuenteConfig] = Field(default_factory=list)
    migracion: MigracionConfig = Field(default_factory=MigracionConfig)
    comercial: ComercialConfig
    promedio: AveragingConfig = Field(default_factory=AveragingConfig)
    carga: LoadConfig
    senales: SignalsConfig = Field(default_factory=SignalsConfig)
    segmentacion: SegmentacionConfig = Field(default_factory=SegmentacionConfig)
    catalogos: CatalogosConfig = Field(default_factory=CatalogosConfig)
    perdidas: PerdidasConfig = Field(default_factory=PerdidasConfig)
    alumbrado: AlumbradoConfig = Field(default_factory=AlumbradoConfig)
    balance: BalanceConfig = Field(default_factory=BalanceConfig)
    cargabilidad: CargabilidadConfig = Field(default_factory=CargabilidadConfig)
    flujo: FlujoConfig = Field(default_factory=FlujoConfig)
    seguridad: SecurityConfig = Field(default_factory=SecurityConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    visor: WebviewerConfig = Field(default_factory=WebviewerConfig)

    def fuente(self, nombre: str) -> FuenteConfig:
        for f in self.fuentes:
            if f.nombre == nombre:
                return f
        raise KeyError(f"fuente '{nombre}' no definida en la configuración")

    def clase(self, tarifa_desc: str) -> ClaseTarifaria | None:
        return self.carga.clases.get(tarifa_desc)
