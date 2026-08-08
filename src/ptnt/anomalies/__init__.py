"""Anomalías estructurales del balance: transferencias, clientes faltantes e
incoherencias por alimentador.

Estas tres familias explican la mayoría de los balances que no cierran, y **no son
hurto**: reportarlas como PNT es el error más caro del proyecto. Se detectan y se
reportan por separado, y los alimentadores afectados se excluyen o degradan en el
ranking de sospecha hasta que se aclaren.
"""

from ptnt.anomalies.coherence import (
    FeederCoherence,
    IncoherenceReport,
    analyze_feeder_coherence,
)
from ptnt.anomalies.transfers import (
    TransferCandidate,
    detect_transfers,
)
from ptnt.anomalies.unmatched import (
    UnmatchedReport,
    analyze_unmatched_customers,
)

__all__ = [
    "detect_transfers",
    "TransferCandidate",
    "analyze_unmatched_customers",
    "UnmatchedReport",
    "analyze_feeder_coherence",
    "FeederCoherence",
    "IncoherenceReport",
]
