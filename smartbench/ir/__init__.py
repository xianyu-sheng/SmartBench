"""Language-neutral semantic intermediate representation.

The :mod:`smartbench.graph` package remains the structural graph used by
older integrations.  This package adds the stable boundary between language
frontends and analysis backends.  Frontends lower source code into
``SemanticIR``; analyzers consume that object without depending on a parser
for a particular language.
"""

from smartbench.ir.capabilities import Capability, CapabilityLevel, CapabilitySet
from smartbench.ir.contracts import (
    CONTRACT_SCHEMA_VERSION,
    BindingContract,
    CallContract,
    FunctionContract,
    ParameterContract,
    validate_operation_contract,
    validate_semantic_ir,
)
from smartbench.ir.evidence import EvidencePack, EvidenceRef, FactKind, SemanticFact
from smartbench.ir.operations import (
    DataFlowKind,
    OperationEdge,
    OperationEdgeKind,
    OperationKind,
    SemanticOperation,
)
from smartbench.ir.provenance import SourceRole, classify_source_role
from smartbench.ir.schema import AnalysisAssessment, SemanticIR, SourceUnit

__all__ = [
    "Capability",
    "CapabilityLevel",
    "CapabilitySet",
    "CONTRACT_SCHEMA_VERSION",
    "BindingContract",
    "CallContract",
    "FunctionContract",
    "ParameterContract",
    "validate_operation_contract",
    "validate_semantic_ir",
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
    "SourceRole",
    "classify_source_role",
    "AnalysisAssessment",
]
