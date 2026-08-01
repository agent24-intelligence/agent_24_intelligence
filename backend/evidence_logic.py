"""Deterministic evidence normalization, linkage, scoring, and classification."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from evidence_models import (
    AdoptionCluster,
    AdoptionEvidenceRecord,
    CandidateConnection,
    ClusterLink,
    GapType,
    ResearchCluster,
)
from scoring_config import (
    ADOPTION_POINTS,
    BASE_ADOPTION_MAX,
    EVIDENCE_WEIGHTS,
    LABEL_THRESHOLDS,
    LINK_THRESHOLDS,
    LINK_WEIGHTS,
    MAX_CANDIDATE_CONNECTIONS,
    ORGANIZATION_MAX,
    QUERY_FAMILY_MAX,
    VENDOR_PRODUCT_INTEGRATION_POINTS,
)


def canonical_url(url: str | None) -> str:
    if not url:
        return ""
    parts = urlsplit(url.strip())
    query = [(key, value) for key, value in parse_qsl(parts.query) if key not in {"utm_source", "utm_campaign", "utm_medium", "ref", "fbclid"}]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), ""))


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    value = value.casefold().strip()
    value = re.sub(r"\b(incorporated|corporation|company|limited|ltd|inc|corp|co)\.?\b", "", value)
    value = re.sub(r"[^\w\s가-힣+.#/-]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def stable_id(prefix: str, *parts: str | None) -> str:
    raw = "|".join(normalize_text(part) for part in parts if part)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def source_id(item: dict[str, Any]) -> str:
    doi = item.get("doi") or item.get("DOI")
    if doi:
        return f"doi:{normalize_text(str(doi)).removeprefix('doi:')}"
    url = canonical_url(item.get("url") or item.get("link"))
    if url:
        return f"url:{url}"
    return stable_id("source", item.get("title"), item.get("published_at"), item.get("publishedAt"))


def _first(*values: str | None) -> str | None:
    return next((value for value in values if value), None)


def _canonical_field(value: str | None) -> str | None:
    normalized = normalize_text(value)
    return normalized or None


def deduplicate_records(records: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for record in records:
        key = f"{record.source_id}|{normalize_text(record.evidence_span)}"
        if key in seen:
            continue
        seen.add(key)
        result.append(record)
    return result


def build_research_clusters(records: Iterable[Any], limit: int = 5) -> list[ResearchCluster]:
    buckets: dict[tuple[str, str, str, str], list[Any]] = defaultdict(list)
    for record in records:
        if not record.technology_canonical:
            continue
        key = (
            record.technology_canonical,
            record.use_case_canonical or "",
            record.context_canonical or "",
            record.expected_value_canonical or "",
        )
        buckets[key].append(record)

    clusters: list[ResearchCluster] = []
    for key, items in buckets.items():
        technology, use_case, context, expected_value = key
        cluster = ResearchCluster(
            cluster_id=stable_id("research", *key),
            technology=technology,
            use_case=use_case or None,
            context=context or None,
            expected_value=expected_value or None,
            evidence_ids=[item.record_id for item in items],
            source_urls=list(dict.fromkeys(item.source_url for item in items)),
            unique_paper_count=len({item.source_id for item in items}),
            replication_count=sum(item.is_replication for item in items),
            synthesis_count=sum(item.is_synthesis for item in items),
            real_world_count=sum(item.is_real_world for item in items),
            supports_count=sum(item.result_direction == "supports" for item in items),
            mixed_count=sum(item.result_direction == "mixed" for item in items),
            contradicts_count=sum(item.result_direction == "contradicts" for item in items),
            unclear_count=sum(item.result_direction == "unclear" for item in items),
        )
        cluster.evidence_maturity = calculate_evidence_maturity(cluster)
        clusters.append(cluster)
    clusters.sort(key=lambda cluster: (cluster.evidence_maturity or 0, cluster.unique_paper_count), reverse=True)
    return clusters[:limit]


def _event_sort_key(record: Any) -> tuple[str, str]:
    return (record.event_date or "", record.record_id)


def build_adoption_clusters(records: Iterable[AdoptionEvidenceRecord], limit: int = 5) -> list[AdoptionCluster]:
    buckets: dict[tuple[str, str, str, str, str], list[AdoptionEvidenceRecord]] = defaultdict(list)
    for record in records:
        if not record.subject_canonical or not record.technology_canonical:
            continue
        key = (
            record.subject_canonical,
            record.technology_canonical,
            record.use_case_canonical or "",
            record.context_canonical or "",
            record.project_name or record.deployment_unit or "",
        )
        buckets[key].append(record)

    stage_rank = {None: -1, "unknown": 0, "pilot": 1, "limited_deployment": 2, "production": 3}
    clusters: list[AdoptionCluster] = []
    for key, items in buckets.items():
        items = sorted(items, key=_event_sort_key)
        latest = items[-1]
        max_stage = max((item.adoption_stage for item in items), key=lambda stage: stage_rank.get(stage, 0), default=None)
        first_date = next((item.event_date for item in items if item.event_date), None)
        clusters.append(
            AdoptionCluster(
                cluster_id=stable_id("adoption", *key),
                subject=key[0],
                technology=key[1],
                use_case=key[2] or None,
                context=key[3] or None,
                expected_value=_first(*(item.expected_value_canonical for item in items)),
                usage_context=latest.usage_context,
                max_stage_attained=max_stage,
                latest_relation=latest.relation,
                deployment_unit=latest.deployment_unit,
                project_name=latest.project_name,
                first_event_date=first_date,
                latest_event_date=latest.event_date,
                evidence_ids=[item.record_id for item in items],
                source_urls=list(dict.fromkeys(item.source_url for item in items)),
                independent_source_count=len({item.source_id for item in items}),
                explicit_barriers=list(dict.fromkeys(barrier for item in items for barrier in item.explicit_barriers)),
            )
        )
    clusters.sort(key=lambda cluster: (stage_rank.get(cluster.max_stage_attained, 0), cluster.independent_source_count), reverse=True)
    return clusters[:limit]


def calculate_evidence_maturity(cluster: ResearchCluster) -> int:
    breadth = min(cluster.unique_paper_count, 5) / 5 * EVIDENCE_WEIGHTS["breadth"]
    replication = min(cluster.replication_count, 2) / 2 * EVIDENCE_WEIGHTS["replication"]
    synthesis = min(cluster.synthesis_count, 1) * EVIDENCE_WEIGHTS["synthesis"]
    real_world = min(cluster.real_world_count, 2) / 2 * EVIDENCE_WEIGHTS["real_world"]
    classified = cluster.supports_count + cluster.mixed_count + cluster.contradicts_count
    if classified == 0:
        direction = 0
    else:
        direction = (
            (cluster.supports_count + 0.5 * cluster.mixed_count) / classified * EVIDENCE_WEIGHTS["direction"]
        )
    return round(breadth + replication + synthesis + real_world + direction)


def calculate_link_similarity(
    technology_match: float,
    use_case_match: float,
    context_match: float,
    expected_value_match: float,
) -> float:
    values = {
        "technology": technology_match,
        "use_case": use_case_match,
        "context": context_match,
        "expected_value": expected_value_match,
    }
    return round(sum(values[key] * LINK_WEIGHTS[key] for key in values), 4)


def classify_link(
    *,
    similarity: float,
    technology_match: float,
    use_case_match: float,
    latest_relation: str | None,
) -> str:
    if latest_relation == "does_not_use" and similarity >= LINK_THRESHOLDS["partial"]:
        return "blocked"
    # A direct adoption claim must match the actual technology and use case.
    # Neighboring use cases remain useful as leads, but are not direct adoption.
    if (
        latest_relation == "uses"
        and technology_match >= 1.0
        and use_case_match >= 1.0
        and similarity >= LINK_THRESHOLDS["direct"]
    ):
        return "direct"
    if latest_relation == "uses" and similarity >= LINK_THRESHOLDS["partial"]:
        return "partial"
    return "unlinked"


def make_cluster_link(
    research: ResearchCluster,
    adoption: AdoptionCluster | None,
    dimensions: dict[str, float] | None = None,
    *,
    explanation: str = "",
    confidence: float = 0.0,
    matched_on: list[str] | None = None,
    missing_on: list[str] | None = None,
) -> ClusterLink:
    dimensions = dimensions or {key: 0.0 for key in LINK_WEIGHTS}
    similarity = calculate_link_similarity(
        dimensions["technology"],
        dimensions["use_case"],
        dimensions["context"],
        dimensions["expected_value"],
    )
    link_type = classify_link(
        similarity=similarity,
        technology_match=dimensions["technology"],
        use_case_match=dimensions["use_case"],
        latest_relation=adoption.latest_relation if adoption else None,
    )
    evidence_ids = list(research.evidence_ids)
    if adoption:
        evidence_ids.extend(adoption.evidence_ids)
    return ClusterLink(
        link_id=stable_id("link", research.cluster_id, adoption.cluster_id if adoption else "unlinked"),
        research_cluster_id=research.cluster_id,
        adoption_cluster_id=adoption.cluster_id if adoption else None,
        technology_match=dimensions["technology"],
        use_case_match=dimensions["use_case"],
        context_match=dimensions["context"],
        expected_value_match=dimensions["expected_value"],
        link_similarity=similarity,
        link_type=link_type,
        matched_on=matched_on or [],
        missing_on=missing_on or [],
        explanation=explanation,
        confidence=max(0.0, min(confidence, 1.0)),
        evidence_ids=evidence_ids,
    )


def calculate_adoption_evidence(
    links: Iterable[ClusterLink],
    adoption_clusters: dict[str, AdoptionCluster],
) -> dict[str, Any]:
    by_org: dict[str, list[tuple[str, float]]] = defaultdict(list)
    cluster_scores: dict[str, float] = {}
    direct_production_orgs: set[str] = set()
    partial_production_orgs: set[str] = set()
    connected_orgs: set[str] = set()
    adjacent_orgs: set[str] = set()
    adjacent_cluster_ids: set[str] = set()
    has_direct_link = False
    for link in links:
        if link.adoption_cluster_id is None or link.link_type not in {"direct", "partial"}:
            continue
        cluster = adoption_clusters.get(link.adoption_cluster_id)
        if not cluster or not cluster.subject:
            continue
        if link.link_type == "partial":
            adjacent_orgs.add(cluster.subject)
            adjacent_cluster_ids.add(cluster.cluster_id)
        else:
            has_direct_link = True
        if cluster.usage_context == "vendor_product_integration":
            points = VENDOR_PRODUCT_INTEGRATION_POINTS
        else:
            context_points = ADOPTION_POINTS.get(cluster.usage_context or "", {})
            stage_points = context_points.get(link.link_type, {})
            points = stage_points.get(cluster.max_stage_attained or "unknown", 0)
        cluster_scores[cluster.cluster_id] = max(cluster_scores.get(cluster.cluster_id, 0), points)
        by_org[cluster.subject].append((cluster.cluster_id, float(points)))
        connected_orgs.add(cluster.subject)
        if link.link_type == "direct" and cluster.max_stage_attained == "production":
            direct_production_orgs.add(cluster.subject)
        if link.link_type == "partial" and cluster.max_stage_attained == "production":
            partial_production_orgs.add(cluster.subject)

    organization_scores: dict[str, float] = {}
    for subject, scores in by_org.items():
        ordered = sorted((score for _, score in scores), reverse=True)
        weighted = sum(score * (1 if index == 0 else 0.5 if index == 1 else 0) for index, score in enumerate(ordered))
        organization_scores[subject] = min(weighted, ORGANIZATION_MAX)

    final_user_orgs = len({subject for subject in by_org if any(
        adoption_clusters[cluster_id].usage_context == "end_user_use"
        for cluster_id, _ in by_org[subject]
        if cluster_id in adoption_clusters
    )})
    breadth_bonus = {0: 0, 1: 0, 2: 8, 3: 15, 4: 20}.get(final_user_orgs, 25)
    base_score = min(sum(sorted(organization_scores.values(), reverse=True)[:3]), BASE_ADOPTION_MAX)
    total = min(round(base_score + breadth_bonus), 100)
    if not has_direct_link:
        total = min(total, LABEL_THRESHOLDS["gap_max_adoption"])
    return {
        "total": total,
        "signals": {
            "base_score": base_score,
            "breadth_bonus": breadth_bonus,
            "partial_only_cap_applied": not has_direct_link and total > 0,
            "final_user_orgs": final_user_orgs,
            "direct_production_orgs": len(direct_production_orgs),
            "partial_production_orgs": len(partial_production_orgs),
            "connected_orgs": len(connected_orgs),
            "adjacent_orgs": len(adjacent_orgs),
            "adjacent_clusters": len(adjacent_cluster_ids),
        },
        "details": {
            "cluster_scores": cluster_scores,
            "organization_scores": organization_scores,
            "direct_production_orgs": sorted(direct_production_orgs),
            "partial_production_orgs": sorted(partial_production_orgs),
            "connected_orgs": sorted(connected_orgs),
            "adjacent_orgs": sorted(adjacent_orgs),
            "adjacent_cluster_ids": sorted(adjacent_cluster_ids),
        },
    }


def calculate_coverage_confidence(
    *,
    unique_academic_sources: int,
    unique_web_sources: int,
    query_family_count: int,
    mapping_confidence: float,
    structured_record_count: int,
    total_relevant_results: int,
    adversarial_performed: bool,
    adversarial_timed_out: bool,
    adversarial_result_count: int,
    adversarial_skipped_with_adoption: bool = False,
) -> dict[str, Any]:
    scholar = min(unique_academic_sources / 5, 1.0) * 20
    web = min(unique_web_sources / 8, 1.0) * 20
    query = min(query_family_count, QUERY_FAMILY_MAX) / QUERY_FAMILY_MAX * 15
    mapping = max(0.0, min(mapping_confidence, 1.0)) * 15
    extraction = 0 if total_relevant_results == 0 else structured_record_count / total_relevant_results * 10
    if adversarial_skipped_with_adoption:
        adversarial = 20
    elif not adversarial_performed:
        adversarial = 0
    elif adversarial_timed_out:
        adversarial = 5
    elif adversarial_result_count == 0:
        adversarial = 12
    elif adversarial_result_count == 1:
        adversarial = 16
    else:
        adversarial = 20
    total = round(scholar + web + query + mapping + extraction + adversarial)
    return {
        "total": max(0, min(total, 100)),
        "signals": {
            "scholar_coverage": round(scholar, 2),
            "web_coverage": round(web, 2),
            "query_coverage": round(query, 2),
            "mapping_score": round(mapping, 2),
            "extraction_score": round(extraction, 2),
            "adversarial_score": adversarial,
        },
        "details": {
            "unique_academic_sources": unique_academic_sources,
            "unique_web_sources": unique_web_sources,
            "query_family_count": query_family_count,
            "structured_record_count": structured_record_count,
            "total_relevant_results": total_relevant_results,
            "adversarial_performed": adversarial_performed,
            "adversarial_timed_out": adversarial_timed_out,
            "adversarial_result_count": adversarial_result_count,
            "adversarial_skipped_with_adoption": adversarial_skipped_with_adoption,
        },
    }


def classify_gap_types(
    research: ResearchCluster,
    links: Iterable[ClusterLink],
    adoption_clusters: dict[str, AdoptionCluster],
    coverage_confidence: int,
) -> list[GapType]:
    links = list(links)
    direct = [link for link in links if link.link_type == "direct"]
    partial = [link for link in links if link.link_type == "partial"]
    blocked = [link for link in links if link.link_type == "blocked"]
    gap_types: list[GapType] = []
    if not direct:
        gap_types.append("no_adoption_link" if coverage_confidence >= LABEL_THRESHOLDS["gap_coverage"] else "possible_no_adoption_link")
    for link in direct + partial:
        adoption = adoption_clusters.get(link.adoption_cluster_id or "")
        if adoption and adoption.max_stage_attained in {"pilot", "limited_deployment"}:
            if "stage_gap" not in gap_types:
                gap_types.append("stage_gap")
        if link.technology_match >= 0.5 and link.use_case_match >= 0.5 and link.context_match == 0.0:
            if "context_gap" not in gap_types:
                gap_types.append("context_gap")
    for link in links:
        adoption = adoption_clusters.get(link.adoption_cluster_id or "")
        if (
            adoption
            and link.technology_match == 0.0
            and link.use_case_match >= 0.5
            and link.context_match >= 0.5
            and adoption.max_stage_attained == "production"
        ):
            if "technology_substitution" not in gap_types:
                gap_types.append("technology_substitution")
    if blocked:
        gap_types.append("barrier_gap")
    return gap_types


def classify_final_label(
    *,
    field_not_confirmed: bool,
    evidence_maturity: int,
    adoption_evidence: int,
    coverage_confidence: int,
    direct_production_org_count: int,
) -> str:
    if field_not_confirmed:
        return "unconfirmed_field"
    if adoption_evidence >= LABEL_THRESHOLDS["no_gap_adoption"] or direct_production_org_count >= LABEL_THRESHOLDS["no_gap_direct_production_orgs"]:
        return "no_gap"
    if adoption_evidence > 0:
        return "emerging_adoption"
    if coverage_confidence < LABEL_THRESHOLDS["min_coverage"] or evidence_maturity < LABEL_THRESHOLDS["min_evidence"]:
        return "insufficient_evidence"
    if (
        evidence_maturity >= LABEL_THRESHOLDS["gap_evidence"]
        and adoption_evidence <= LABEL_THRESHOLDS["gap_max_adoption"]
        and coverage_confidence >= LABEL_THRESHOLDS["gap_coverage"]
    ):
        return "gap_candidate"
    return "emerging_adoption"


def calculate_gap_priority(evidence_maturity: int, adoption_evidence: int, coverage_confidence: int) -> int:
    return round(evidence_maturity * (100 - adoption_evidence) / 100 * coverage_confidence / 100)


def build_candidate_connections(links: Iterable[ClusterLink]) -> list[CandidateConnection]:
    candidates = [
        link for link in links
        if link.adoption_cluster_id and link.link_type != "direct" and link.link_similarity >= LINK_THRESHOLDS["candidate"]
    ]
    candidates.sort(key=lambda link: link.link_similarity, reverse=True)
    return [
        CandidateConnection(
            research_cluster_id=link.research_cluster_id,
            adoption_cluster_id=link.adoption_cluster_id,
            connection_basis=link.matched_on,
            missing_dimensions=link.missing_on,
            required_validation=link.missing_on,
            explanation=link.explanation,
            confidence=link.confidence,
        )
        for link in candidates[:MAX_CANDIDATE_CONNECTIONS]
    ]


def should_deep_research(
    *,
    contradicts_count: int,
    evidence_maturity: int,
    direct_links_exist: bool,
    blocked_links_exist: bool,
    gap_priority: int,
    coverage_confidence: int,
    high_impact_candidate: bool,
    barrier_reason_unknown: bool,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if contradicts_count > 0 and evidence_maturity >= 60:
        reasons.append("학술 결과에 반대 방향 근거가 있고 성숙도가 60 이상")
    if direct_links_exist and blocked_links_exist:
        reasons.append("직접 도입과 중단 증거가 동시에 존재")
    if gap_priority >= LABEL_THRESHOLDS["deep_research_priority"] and coverage_confidence < LABEL_THRESHOLDS["gap_coverage"]:
        reasons.append("우선순위 높은 갭 후보지만 검색 커버리지가 부족")
    if high_impact_candidate and barrier_reason_unknown:
        reasons.append("상위 후보의 장벽 원인이 확인되지 않음")
    return bool(reasons), reasons
