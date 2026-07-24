"""Language-neutral semantic intermediate representation.

The :mod:`smartbench.graph` package remains the structural graph used by
older integrations.  This package adds the stable boundary between language
frontends and analysis backends.  Frontends lower source code into
``SemanticIR``; analyzers consume that object without depending on a parser
for a particular language.
"""

from smartbench.ir.capabilities import Capability, CapabilitySet
from smartbench.ir.evidence import EvidencePack, EvidenceRef, FactKind, SemanticFact
from smartbench.ir.operations import (
    DataFlowKind,
    OperationEdge,
    OperationEdgeKind,
    OperationKind,
    SemanticOperation,
)
from smartbench.ir.schema import SemanticIR, SourceUnit

__all__ = [
    "Capability",
    "CapabilitySet",
    "EvidencePack",
    "EvidenceRef",
    "FactKind",
    "SemanticFact",
    "DataFlowKind",
    "OperationEdge",
    "OperationEdgeKind",
    "OperationKind",
    "SemanticOperation",
    "SemanticIR",
    "SourceUnit",
]
