"""Focalización de levantamientos de campo (§11.5).

Responde la pregunta operativa del proyecto: **¿dónde hay que ir a hacer el
levantamiento?** Produce los rankings multinivel —alimentador, zona de protección,
ramal, puesto de transformación y cliente— más los **sectores de sospecha**
(agrupamiento geográfico), y de ahí genera el **plan de campo** con órdenes de
levantamiento priorizadas, exportable y visible en las interfaces.
"""

from ptnt.survey.routes import (
    RouteStats,
    analyze_commercial_routes,
    routes_to_dataframe,
    routes_to_target_input,
)
from ptnt.survey.sectors import Sector, cluster_sectors
from ptnt.survey.targeting import (
    SurveyPlan,
    SurveyTarget,
    TargetLevel,
    build_survey_plan,
)

__all__ = [
    "build_survey_plan",
    "SurveyPlan",
    "SurveyTarget",
    "TargetLevel",
    "cluster_sectors",
    "Sector",
    "analyze_commercial_routes",
    "RouteStats",
    "routes_to_target_input",
    "routes_to_dataframe",
]
