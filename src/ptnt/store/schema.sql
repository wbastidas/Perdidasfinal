-- =============================================================================
-- PTNT-BAL — Esquema de base de datos local (DuckDB)
-- Los volúmenes grandes (tramos, clientes, series de consumo) viven en Parquet
-- particionado y se exponen aquí como vistas. Las tablas materializadas son
-- catálogos, metadatos de ejecución y resultados agregados.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS meta;
CREATE SCHEMA IF NOT EXISTS ref;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

-- =============================================================================
-- META — estado de ejecución y versionado
-- =============================================================================

-- Versionado de red por alimentador.
-- Implementa "alimentador nuevo -> alta ; alimentador existente -> actualización".
CREATE TABLE IF NOT EXISTS meta.feeder_version (
    feeder_code           VARCHAR       NOT NULL,
    version_id            INTEGER       NOT NULL,
    loaded_at             TIMESTAMP     NOT NULL,
    source_fgdb           VARCHAR       NOT NULL,
    source_fgdb_hash      VARCHAR       NOT NULL,
    topology_hash         VARCHAR       NOT NULL,
    attribute_hash        VARCHAR       NOT NULL,
    switch_state_hash     VARCHAR       NOT NULL,
    element_counts        JSON,
    change_summary        JSON,
    is_current            BOOLEAN       NOT NULL DEFAULT TRUE,
    valid_from            TIMESTAMP     NOT NULL,
    valid_to              TIMESTAMP,
    PRIMARY KEY (feeder_code, version_id)
);

CREATE TABLE IF NOT EXISTS meta.run (
    run_id                UUID          PRIMARY KEY,
    parent_run_id         UUID,
    stage                 VARCHAR       NOT NULL,   -- E1..E12
    feeder_code           VARCHAR,                  -- NULL = corrida global
    started_at            TIMESTAMP     NOT NULL,
    finished_at           TIMESTAMP,
    status                VARCHAR       NOT NULL,   -- OK|ERROR|SKIPPED|PARTIAL
    input_hash            VARCHAR,
    config_hash           VARCHAR,
    config_snapshot       JSON,
    metrics               JSON,
    error_detail          VARCHAR
);

CREATE TABLE IF NOT EXISTS meta.checkpoint (
    stage                 VARCHAR       NOT NULL,
    feeder_code           VARCHAR       NOT NULL,
    input_hash            VARCHAR       NOT NULL,
    config_hash           VARCHAR       NOT NULL,
    output_path           VARCHAR,
    completed_at          TIMESTAMP     NOT NULL,
    PRIMARY KEY (stage, feeder_code)
);

CREATE TABLE IF NOT EXISTS meta.ingest_summary (
    run_id                UUID          NOT NULL,
    feeder_code           VARCHAR,
    fgdb_class            VARCHAR       NOT NULL,   -- nombre ORIGINAL de la clase
    record_count          BIGINT        NOT NULL,
    field_count           INTEGER,
    null_critical_fields  JSON,
    ingested_at           TIMESTAMP     NOT NULL
);

CREATE TABLE IF NOT EXISTS meta.data_lineage (
    entity                VARCHAR       NOT NULL,
    field                 VARCHAR       NOT NULL,
    source_class          VARCHAR,
    source_field          VARCHAR,
    transform             VARCHAR,
    origen_valor_default  VARCHAR       -- MEDIDO|CATALOGO|ESTIMADO_MODELO|SUPUESTO_DEFECTO|INFERIDO_TOPOLOGIA
);

-- =============================================================================
-- REF — catálogos
-- =============================================================================

-- Dominios cargados desde el modelo de datos entregado. NO se transcriben a mano.
CREATE TABLE IF NOT EXISTS ref.domain_value (
    domain_name           VARCHAR       NOT NULL,
    code                  VARCHAR       NOT NULL,
    label                 VARCHAR       NOT NULL,
    field_type            VARCHAR,
    is_unit_variable      BOOLEAN       DEFAULT FALSE,  -- Codigo Alimentador, Numero Estacion, Subestacion
    PRIMARY KEY (domain_name, code)
);

CREATE TABLE IF NOT EXISTS ref.conductor (
    conductor_code        VARCHAR       PRIMARY KEY,    -- cruza con dominio 'Catalogo Conductores'
    conductor_name        VARCHAR,
    material              VARCHAR,                      -- AL|CU|ACSR|...
    size_awg_kcmil        VARCHAR,
    section_mm2           DOUBLE,
    r_ohm_km_20c          DOUBLE        NOT NULL,
    x_ohm_km              DOUBLE        NOT NULL,
    r0_ohm_km             DOUBLE,
    x0_ohm_km             DOUBLE,
    ampacity_a            DOUBLE        NOT NULL,
    gmr_m                 DOUBLE,
    diameter_mm           DOUBLE,
    is_underground        BOOLEAN,
    voltage_class         VARCHAR,                      -- BT|MT|AT
    source                VARCHAR
);

CREATE TABLE IF NOT EXISTS ref.transformer_catalog (
    kva                   DOUBLE        NOT NULL,
    phases                INTEGER       NOT NULL,
    voltage_class         VARCHAR       NOT NULL,
    norm                  VARCHAR,
    p0_kw                 DOUBLE        NOT NULL,       -- pérdida en vacío
    pk_kw                 DOUBLE        NOT NULL,       -- pérdida con carga nominal
    z_pct                 DOUBLE        NOT NULL,
    xr_ratio              DOUBLE,
    source                VARCHAR,
    PRIMARY KEY (kva, phases, voltage_class, norm)
);

CREATE TABLE IF NOT EXISTS ref.streetlight_catalog (
    structure_code        VARCHAR       PRIMARY KEY,    -- dominios UP_AP_*
    technology            VARCHAR       NOT NULL,
    lamp_w                DOUBLE        NOT NULL,
    auxiliary_w           DOUBLE        NOT NULL,
    source                VARCHAR
);

CREATE TABLE IF NOT EXISTS ref.tariff_class (
    tariff_code           VARCHAR       PRIMARY KEY,
    tariff_description    VARCHAR,                      -- p.ej. 'BT Residencial' del CSV
    class_group           VARCHAR,
    velander_a            DOUBLE        NOT NULL,
    velander_b            DOUBLE        NOT NULL,
    coincidence_a         DOUBLE        NOT NULL,
    coincidence_b         DOUBLE        NOT NULL,
    cos_phi               DOUBLE        NOT NULL,
    load_factor           DOUBLE        NOT NULL,
    k_loss                DOUBLE        NOT NULL,
    origen_valor          VARCHAR       NOT NULL
);

CREATE TABLE IF NOT EXISTS ref.load_profile (
    profile_id            VARCHAR       NOT NULL,
    tariff_code           VARCHAR,
    day_type              VARCHAR       NOT NULL,       -- LABORAL|SABADO|DOMINGO
    hour                  INTEGER       NOT NULL,       -- 0..23
    mult                  DOUBLE        NOT NULL,       -- normalizado, max = 1.0
    PRIMARY KEY (profile_id, day_type, hour)
);

CREATE TABLE IF NOT EXISTS ref.calibration (
    calibration_id        UUID          PRIMARY KEY,
    parameter             VARCHAR       NOT NULL,
    scope                 VARCHAR       NOT NULL,       -- GLOBAL|TIPO_ALIMENTADOR|ALIMENTADOR|CLASE
    scope_value           VARCHAR,
    value                 DOUBLE        NOT NULL,
    fit_metric            DOUBLE,
    n_observations        INTEGER,
    calibrated_at         TIMESTAMP     NOT NULL,
    method                VARCHAR
);

-- =============================================================================
-- SILVER — modelo canónico (vistas sobre Parquet + tablas de series)
-- =============================================================================

-- Serie de consumo en formato LARGO (no ancho).
CREATE TABLE IF NOT EXISTS silver.customer_consumption (
    contract_account      VARCHAR       NOT NULL,
    period                DATE          NOT NULL,
    kwh                   DOUBLE,
    is_zero               BOOLEAN,
    is_estimated          BOOLEAN       DEFAULT FALSE,
    is_negative_flag      BOOLEAN       DEFAULT FALSE,
    load_batch_id         UUID          NOT NULL,
    PRIMARY KEY (contract_account, period)
);

CREATE TABLE IF NOT EXISTS silver.feeder_head_energy (
    feeder_code           VARCHAR       NOT NULL,
    period                DATE          NOT NULL,
    kwh_delivered         DOUBLE        NOT NULL,
    kvarh                 DOUBLE,
    kw_max_demand         DOUBLE,
    kva_max_demand        DOUBLE,
    pf_at_max             DOUBLE,
    hours_period          DOUBLE        NOT NULL,
    load_factor_measured  DOUBLE,
    source                VARCHAR,
    origen_valor          VARCHAR       NOT NULL,
    PRIMARY KEY (feeder_code, period)
);

-- Vinculación comercial <-> SIG
CREATE TABLE IF NOT EXISTS silver.customer_link (
    contract_account      VARCHAR       NOT NULL,
    customer_unit_id      VARCHAR,                      -- CONEXIONCONSUMIDOR.GLOBALID
    unique_code           VARCHAR,                      -- CODIGOUNICO
    client_code           VARCHAR,                      -- CODIGOCLIENTE
    customer_site_id      VARCHAR,                      -- PuntoCarga.GLOBALID
    feeder_code_declared  VARCHAR,
    feeder_code_traced    VARCHAR,
    transformer_site_declared VARCHAR,
    transformer_site_traced   VARCHAR,
    match_method          VARCHAR       NOT NULL,       -- CUENTA_CONTRATO|CODIGO_CLIENTE|ESPACIAL|SIN_MATCH
    match_confidence      DOUBLE        NOT NULL,
    distance_to_site_m    DOUBLE,
    linked_at             TIMESTAMP     NOT NULL,
    PRIMARY KEY (contract_account)
);

-- Potencias recalculadas por cliente y período
CREATE TABLE IF NOT EXISTS silver.customer_power (
    customer_unit_id      VARCHAR       NOT NULL,
    period                DATE          NOT NULL,
    kwh                   DOUBLE,
    p_avg_kw              DOUBLE,
    p_max_ind_kw          DOUBLE,
    q_kvar                DOUBLE,
    s_kva                 DOUBLE,
    current_a             DOUBLE,
    phase_config          VARCHAR,                      -- 1F|2F|3F
    voltage_v             DOUBLE,
    cos_phi_used          DOUBLE,
    method                VARCHAR,                      -- VELANDER|FACTOR_CARGA
    origen_valor          VARCHAR       NOT NULL,
    -- valores previos del SIG, para el informe de reconciliación
    p_kw_previous         DOUBLE,
    q_kvar_previous       DOUBLE,
    PRIMARY KEY (customer_unit_id, period)
);

-- =============================================================================
-- GOLD — resultados
-- =============================================================================

CREATE TABLE IF NOT EXISTS gold.quality_finding (
    finding_id            UUID          PRIMARY KEY,
    run_id                UUID          NOT NULL,
    rule_id               VARCHAR       NOT NULL,       -- R01..R32, P01..P12, T01..T09, AP01..AP06
    severity              VARCHAR       NOT NULL,       -- CRITICA|ALTA|MEDIA|BAJA
    feeder_code           VARCHAR,
    element_class         VARCHAR       NOT NULL,       -- nombre ORIGINAL de la clase FGDB
    element_id            VARCHAR       NOT NULL,       -- GLOBALID
    related_element_id    VARCHAR,
    evidence              JSON          NOT NULL,
    suggested_value       VARCHAR,
    confidence            DOUBLE        NOT NULL DEFAULT 1.0,
    geom_wkb              BLOB,
    x                     DOUBLE,
    y                     DOUBLE,
    detected_at           TIMESTAMP     NOT NULL,
    status                VARCHAR       NOT NULL DEFAULT 'ABIERTO',
    status_note           VARCHAR,
    status_by             VARCHAR,
    status_at             TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gold.model_confidence (
    run_id                UUID          NOT NULL,
    level                 VARCHAR       NOT NULL,       -- ALIMENTADOR|ZONA_PROTECCION
    entity_id             VARCHAR       NOT NULL,
    feeder_code           VARCHAR,
    confidence_index      DOUBLE        NOT NULL,       -- 0..100
    findings_critical     INTEGER,
    findings_high         INTEGER,
    findings_medium       INTEGER,
    pct_attributes_complete   DOUBLE,
    pct_customers_traced      DOUBLE,
    pct_energy_measured       DOUBLE,
    pct_topology_reachable    DOUBLE,
    computed_at           TIMESTAMP     NOT NULL,
    PRIMARY KEY (run_id, level, entity_id)
);

CREATE TABLE IF NOT EXISTS gold.technical_loss_component (
    run_id                UUID          NOT NULL,
    period_start          DATE          NOT NULL,
    period_end            DATE          NOT NULL,
    feeder_code           VARCHAR       NOT NULL,
    level                 VARCHAR       NOT NULL,       -- ALIMENTADOR|ZONA_PROTECCION|RAMAL|PUESTO|TRAMO|UNIDAD
    entity_id             VARCHAR       NOT NULL,
    component             VARCHAR       NOT NULL,       -- SUBTRANSMISION|TRANSF_POTENCIA|RED_MT|
                                                        -- TRANSF_DIST_VACIO|TRANSF_DIST_CARGA|RED_BT|
                                                        -- NEUTRO|ACOMETIDAS|MEDIDORES|CIRCUITOS_AP
    loss_kwh              DOUBLE        NOT NULL,
    loss_kw_peak          DOUBLE,
    loss_kwh_p10          DOUBLE,
    loss_kwh_p50          DOUBLE,
    loss_kwh_p90          DOUBLE,
    loss_factor_used      DOUBLE,
    load_factor_used      DOUBLE,
    k_used                DOUBLE,
    origen_valor          VARCHAR       NOT NULL
);

CREATE TABLE IF NOT EXISTS gold.energy_balance (
    balance_id            UUID          PRIMARY KEY,
    run_id                UUID          NOT NULL,
    period_start          DATE          NOT NULL,
    period_end            DATE          NOT NULL,
    level                 VARCHAR       NOT NULL,       -- ALIMENTADOR|ZONA_PROTECCION|RAMAL|PUESTO_TRANSFORMACION
    entity_id             VARCHAR       NOT NULL,
    feeder_code           VARCHAR       NOT NULL,
    balance_type          VARCHAR       NOT NULL,       -- MEDIDO|INDICATIVO  <-- obligatorio en toda vista
    e_input_kwh                 DOUBLE,
    e_billed_kwh                DOUBLE,
    e_streetlight_unmetered_kwh DOUBLE,
    e_streetlight_metered_kwh   DOUBLE,
    e_own_use_kwh               DOUBLE,
    e_not_supplied_kwh          DOUBLE,
    e_transferred_kwh           DOUBLE,
    loss_total_kwh              DOUBLE,
    loss_technical_kwh          DOUBLE,
    loss_technical_p10          DOUBLE,
    loss_technical_p50          DOUBLE,
    loss_technical_p90          DOUBLE,
    ntl_kwh                     DOUBLE,
    ntl_p10                     DOUBLE,
    ntl_p50                     DOUBLE,
    ntl_p90                     DOUBLE,
    ntl_pct                     DOUBLE,
    customers_count             INTEGER,
    customers_traced_pct        DOUBLE,
    energy_linked_pct           DOUBLE,
    energy_estimated_reading_pct DOUBLE,
    assumption_share_pct        DOUBLE,
    model_confidence_index      DOUBLE,
    closure_residual_pct        DOUBLE,
    computed_at                 TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS gold.balance_control (
    control_id            UUID          PRIMARY KEY,
    balance_id            UUID          NOT NULL,
    control_code          VARCHAR       NOT NULL,       -- C01..C06
    triggered             BOOLEAN       NOT NULL,
    observed_value        DOUBLE,
    threshold_value       DOUBLE,
    hypothesis            JSON,                         -- causas ordenadas por sensibilidad
    detail                VARCHAR
);

CREATE TABLE IF NOT EXISTS gold.transformer_loading (
    run_id                UUID          NOT NULL,
    period                DATE          NOT NULL,
    site_id               VARCHAR       NOT NULL,       -- PuestoTransfDistribucion.GLOBALID
    feeder_code           VARCHAR,
    protection_zone_id    VARCHAR,
    unit_count            INTEGER,
    bank_config           VARCHAR,                      -- MONOFASICO|DELTA_ABIERTO|BANCO_3|DELTA_4H|...
    bank_config_confidence DOUBLE,
    kva_installed_sum     DOUBLE,
    kva_capacity_site     DOUBLE,                       -- según configuración de banco
    s_max_diversified_kva DOUBLE,
    loading_ratio         DOUBLE,
    loading_class         VARCHAR,
    customers_count       INTEGER,
    streetlights_count    INTEGER,
    streetlight_kw        DOUBLE,
    energy_billed_kwh     DOUBLE,
    imbalance_pct         DOUBLE,
    neutral_loss_kwh      DOUBLE,
    rebalance_benefit_kwh DOUBLE,
    declared_loading_pct  DOUBLE,                       -- CARGABILIDAD del SIG, para contraste
    PRIMARY KEY (run_id, site_id, period)
);

CREATE TABLE IF NOT EXISTS gold.ntl_signal (
    signal_id             UUID          PRIMARY KEY,
    run_id                UUID          NOT NULL,
    level                 VARCHAR       NOT NULL,       -- ALIMENTADOR|ZONA|RAMAL|PUESTO|PUESTO_CLIENTE|UNIDAD_CLIENTE
    entity_id             VARCHAR       NOT NULL,
    signal_code           VARCHAR       NOT NULL,       -- N1..N6, S1..S10, UNSUP
    signal_value          DOUBLE        NOT NULL,
    signal_rank_pct       DOUBLE,
    confidence            DOUBLE        NOT NULL,
    evidence              JSON          NOT NULL,
    computed_at           TIMESTAMP     NOT NULL
);

CREATE TABLE IF NOT EXISTS gold.ntl_score (
    run_id                UUID          NOT NULL,
    period                DATE          NOT NULL,
    level                 VARCHAR       NOT NULL,
    entity_id             VARCHAR       NOT NULL,
    feeder_code           VARCHAR,
    protection_zone_id    VARCHAR,
    transformer_site_id   VARCHAR,
    score                 DOUBLE        NOT NULL,       -- 0..1 calibrado
    score_rank            INTEGER,
    recoverable_kwh_month DOUBLE,
    top_reasons           JSON          NOT NULL,       -- 3 razones en lenguaje operativo
    active_signals        JSON,
    zone_confidence_index DOUBLE,
    data_problem_flag     BOOLEAN,                      -- score alto en zona de baja confiabilidad
    PRIMARY KEY (run_id, level, entity_id, period)
);

CREATE TABLE IF NOT EXISTS gold.suspect_area (
    area_id               UUID          PRIMARY KEY,
    run_id                UUID          NOT NULL,
    period                DATE          NOT NULL,
    feeder_code           VARCHAR,
    cluster_label         INTEGER,
    entity_count          INTEGER,
    customers_count       INTEGER,
    suspect_energy_kwh    DOUBLE,
    mean_score            DOUBLE,
    confidence_index      DOUBLE,
    centroid_x            DOUBLE,
    centroid_y            DOUBLE,
    hull_wkb              BLOB
);

CREATE TABLE IF NOT EXISTS gold.reconciliation_report (
    run_id                UUID          NOT NULL,
    scope                 VARCHAR       NOT NULL,       -- GLOBAL|ALIMENTADOR|DIVISION|CLASE
    scope_value           VARCHAR,
    p_kw_previous_sum     DOUBLE,
    p_kw_corrected_sum    DOUBLE,
    q_kvar_previous_sum   DOUBLE,
    q_kvar_corrected_sum  DOUBLE,
    delta_p_kw            DOUBLE,
    delta_p_pct           DOUBLE,
    cause_breakdown       JSON,                         -- energia_vs_demanda, factor_sqrt3,
                                                        -- coincidencia, cos_phi, dias_periodo, factor_mult
    n_customers           INTEGER,
    computed_at           TIMESTAMP     NOT NULL
);

CREATE TABLE IF NOT EXISTS gold.unmatched_customer (
    run_id                UUID          NOT NULL,
    contract_account      VARCHAR,
    customer_unit_id      VARCHAR,
    direction             VARCHAR       NOT NULL,       -- CSV_SIN_SIG | SIG_SIN_CSV
    reason                VARCHAR       NOT NULL,
    kwh_last_12m          DOUBLE,
    x                     DOUBLE,
    y                     DOUBLE
);

CREATE TABLE IF NOT EXISTS gold.metering_recommendation (
    run_id                UUID          NOT NULL,
    feeder_code           VARCHAR       NOT NULL,
    candidate_node_id     VARCHAR       NOT NULL,
    candidate_type        VARCHAR,                      -- ZONA_PROTECCION|PUESTO_TRANSFORMACION|RAMAL
    energy_under_uncertainty_kwh DOUBLE,
    customers_downstream  INTEGER,
    uncertainty_reduction_pct    DOUBLE,
    priority_rank         INTEGER
);

-- Esquema listo desde v1.0 aunque se use en v1.1
CREATE TABLE IF NOT EXISTS gold.field_inspection (
    inspection_id         UUID          PRIMARY KEY,
    entity_level          VARCHAR       NOT NULL,
    entity_id             VARCHAR       NOT NULL,
    contract_account      VARCHAR,
    planned_at            DATE,
    executed_at           DATE,
    crew                  VARCHAR,
    finding               BOOLEAN,                      -- hallazgo sí/no
    finding_type          VARCHAR,
    recovered_kwh         DOUBLE,
    real_cost_usd         DOUBLE,
    is_exploration        BOOLEAN       DEFAULT FALSE,  -- muestra aleatoria estratificada
    notes                 VARCHAR,
    photos                JSON,
    score_at_selection    DOUBLE,
    reasons_at_selection  JSON
);

-- =============================================================================
-- Índices
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_finding_feeder    ON gold.quality_finding(feeder_code);
CREATE INDEX IF NOT EXISTS idx_finding_rule      ON gold.quality_finding(rule_id);
CREATE INDEX IF NOT EXISTS idx_finding_severity  ON gold.quality_finding(severity);
CREATE INDEX IF NOT EXISTS idx_balance_feeder    ON gold.energy_balance(feeder_code, period_start);
CREATE INDEX IF NOT EXISTS idx_balance_level     ON gold.energy_balance(level, entity_id);
CREATE INDEX IF NOT EXISTS idx_score_feeder      ON gold.ntl_score(feeder_code, score_rank);
CREATE INDEX IF NOT EXISTS idx_signal_entity     ON gold.ntl_signal(level, entity_id);
CREATE INDEX IF NOT EXISTS idx_consumption_acct  ON silver.customer_consumption(contract_account);
CREATE INDEX IF NOT EXISTS idx_link_unit         ON silver.customer_link(customer_unit_id);
CREATE INDEX IF NOT EXISTS idx_loading_feeder    ON gold.transformer_loading(feeder_code, loading_class);
CREATE INDEX IF NOT EXISTS idx_version_current   ON meta.feeder_version(feeder_code, is_current);
