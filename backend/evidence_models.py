"""Structured evidence, cluster, linkage, and scoring models."""

from typing import Literal

from pydantic import BaseModel, Field


ResultDirection = Literal["supports", "mixed", "contradicts", "unclear"]
Relation = Literal["uses", "does_not_use"] | None
UsageContext = Literal[
    "vendor_product_integration",
    "vendor_internal_use",
    "end_user_use",
] | None
AdoptionStage = Literal["pilot", "limited_deployment", "production", "unknown"] | None
ObjectMatch = Literal["exact", "variant", "related", "unrelated", "unclear"]
LinkType = Literal["direct", "partial", "blocked", "unlinked"]
GapType = Literal[
    "no_adoption_link",
    "possible_no_adoption_link",
    "stage_gap",
    "context_gap",
    "technology_substitution",
    "barrier_gap",
    "outcome_gap",
]
FinalLabel = Literal[
    "unconfirmed_field",
    "insufficient_evidence",
    "gap_candidate",
    "emerging_adoption",
    "no_gap",
]


class BaseEvidenceRecord(BaseModel):
    record_id: str
    source_id: str
    source_url: str
    source_title: str
    published_at: str | None = None
    citation_count: int | None = None

    technology_raw: str | None = None
    technology_canonical: str | None = None
    use_case_raw: str | None = None
    use_case_canonical: str | None = None
    context_raw: str | None = None
    context_canonical: str | None = None
    expected_value_raw: str | None = None
    expected_value_canonical: str | None = None

    canonical_claim: str
    evidence_span: str
    extraction_confidence: float = Field(ge=0, le=1)
    query_family: str | None = None


class AcademicEvidenceRecord(BaseEvidenceRecord):
    record_type: Literal["academic"] = "academic"
    is_replication: bool = False
    is_synthesis: bool = False
    is_real_world: bool = False
    is_counter_evidence: bool = False
    result_direction: ResultDirection = "unclear"
    institutions: list[str] = Field(default_factory=list)


class AdoptionEvidenceRecord(BaseEvidenceRecord):
    record_type: Literal["adoption"] = "adoption"
    subject_raw: str | None = None
    subject_canonical: str | None = None
    relation: Relation = None
    usage_context: UsageContext = None
    adoption_stage: AdoptionStage = None
    deployment_unit: str | None = None
    project_name: str | None = None
    event_date: str | None = None
    explicit_barriers: list[str] = Field(default_factory=list)


class ResearchCluster(BaseModel):
    cluster_id: str
    technology: str
    use_case: str | None = None
    context: str | None = None
    expected_value: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    unique_paper_count: int = 0
    replication_count: int = 0
    synthesis_count: int = 0
    real_world_count: int = 0
    supports_count: int = 0
    mixed_count: int = 0
    contradicts_count: int = 0
    unclear_count: int = 0
    evidence_maturity: int | None = None


class AdoptionCluster(BaseModel):
    cluster_id: str
    subject: str
    technology: str
    use_case: str | None = None
    context: str | None = None
    expected_value: str | None = None
    usage_context: UsageContext = None
    max_stage_attained: AdoptionStage = None
    latest_relation: Relation = None
    deployment_unit: str | None = None
    project_name: str | None = None
    first_event_date: str | None = None
    latest_event_date: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    independent_source_count: int = 0
    explicit_barriers: list[str] = Field(default_factory=list)


class ClusterLink(BaseModel):
    link_id: str
    research_cluster_id: str
    adoption_cluster_id: str | None = None
    technology_match: float = Field(ge=0, le=1)
    use_case_match: float = Field(ge=0, le=1)
    context_match: float = Field(ge=0, le=1)
    expected_value_match: float = Field(ge=0, le=1)
    link_similarity: float = Field(ge=0, le=1)
    link_type: LinkType
    matched_on: list[str] = Field(default_factory=list)
    missing_on: list[str] = Field(default_factory=list)
    explanation: str = ""
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)


class CandidateConnection(BaseModel):
    research_cluster_id: str
    adoption_cluster_id: str | None = None
    connection_basis: list[str] = Field(default_factory=list)
    missing_dimensions: list[str] = Field(default_factory=list)
    required_validation: list[str] = Field(default_factory=list)
    explanation: str = ""
    status: Literal["inferred"] = "inferred"
    confidence: float = Field(ge=0, le=1)


class ScoreBreakdown(BaseModel):
    total: int = Field(ge=0, le=100)
    signals: dict[str, float] = Field(default_factory=dict)
    details: dict[str, object] = Field(default_factory=dict)


class GapAnalysis(BaseModel):
    research_cluster_id: str
    scores: dict[str, int]
    score_breakdown: dict[str, ScoreBreakdown]
    label: FinalLabel
    gap_types: list[GapType] = Field(default_factory=list)
    links: list[ClusterLink] = Field(default_factory=list)
    candidate_connections: list[CandidateConnection] = Field(default_factory=list)
    confirmed_barriers: list[str] = Field(default_factory=list)
    inferred_barriers: list[str] = Field(default_factory=list)
    rationale: str = ""
    connected_points: list[str] = Field(default_factory=list)
    gap_points: list[str] = Field(default_factory=list)
    potential_points: list[str] = Field(default_factory=list)
