"""Engine module — multi-agent debate engine with evidence verification."""

from smartbench.engine.debate import DebateEngine, DebateResult, EvidencePolicy
from smartbench.engine.project_reader import (
    CandidateSemanticMapping,
    DeterministicEvidenceResolver,
    EvidenceResolutionDecision,
    EvidenceResolutionStatus,
    MappingDecision,
    MappingStatus,
    ProjectModel,
    ProjectModelResolution,
    ProjectModelValidation,
    ProjectModelValidator,
    ProjectReaderAgent,
    ProjectReaderResult,
    build_project_inventory,
)

__all__ = [
    "DebateEngine",
    "DebateResult",
    "EvidencePolicy",
    "CandidateSemanticMapping",
    "DeterministicEvidenceResolver",
    "EvidenceResolutionDecision",
    "EvidenceResolutionStatus",
    "MappingDecision",
    "MappingStatus",
    "ProjectModel",
    "ProjectModelResolution",
    "ProjectModelValidation",
    "ProjectModelValidator",
    "ProjectReaderAgent",
    "ProjectReaderResult",
    "build_project_inventory",
]
