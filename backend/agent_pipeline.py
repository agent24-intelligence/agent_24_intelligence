"""Research-to-Reality pipeline with deterministic linkage and scoring."""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urlsplit

from events import emit_event
from evidence_logic import (
    build_adoption_clusters,
    build_candidate_connections,
    build_research_clusters,
    calculate_adoption_evidence,
    calculate_coverage_confidence,
    calculate_gap_priority,
    classify_gap_types,
    classify_final_label,
    deduplicate_records,
    make_cluster_link,
    should_deep_research,
    normalize_text,
    source_id,
    stable_id,
)
from evidence_models import AdoptionEvidenceRecord, AcademicEvidenceRecord, AdoptionCluster, ClusterLink, ResearchCluster
from liner_client import LinerClient
from openai_agents import (
    AdoptionExtractionBatch,
    AcademicExtractionBatch,
    AgentBudgetTimeout,
    QueryFamilies,
    ResearchAgents,
    ScopeDecision,
    VocabularyBridgeResult,
)
from runtime_config import AnalysisDeadline, RuntimeConfig
from scoring_config import MAX_ADOPTION_CLUSTERS, MAX_LINK_CANDIDATES_PER_RESEARCH, MAX_RESEARCH_CLUSTERS


class ResearchPipeline:
    """Run search, structure, linkage, scoring, verification, and visualization."""

    def __init__(
        self,
        liner: LinerClient | None = None,
        agents: ResearchAgents | None = None,
        *,
        deadline: AnalysisDeadline | None = None,
        runtime: RuntimeConfig | None = None,
    ):
        self.runtime = runtime or RuntimeConfig()
        self.liner = liner or LinerClient(disable_timeouts=self.runtime.disable_timeouts)
        self.agents = agents or ResearchAgents()
        self.deadline = deadline
        self.last_partial_result: dict[str, Any] | None = None

    async def run(
        self,
        topic: str,
        *,
        scholar_query: str | None = None,
        adoption_queries: list[str] | None = None,
        max_results: int = 10,
    ) -> dict[str, Any]:
        self.deadline = self.deadline or AnalysisDeadline(self.runtime.total_timeout_s)
        self.last_partial_result = None
        max_results = min(max_results, self.runtime.max_search_results)

        scope = await self._calibrate_scope(topic)
        if scope.status == "unconfirmed" or not scope.selected_topics:
            return self._unconfirmed_result(topic, scope)

        scholar_queries, query_generations = await self._build_scholar_queries(topic, scope, scholar_query)
        scholar = await self._run_scholar_scout(scholar_queries, max_results=max_results)
        scholar_items = _tag_items(scholar.get("results", []), "academic")

        academic_task = asyncio.create_task(self._extract_academic_records(scholar_items))
        vocabulary_task = asyncio.create_task(self._run_vocabulary_bridge(topic, scholar))
        academic_records, academic_extraction_meta = await academic_task
        academic_records = deduplicate_records(academic_records)
        research_clusters = build_research_clusters(academic_records, limit=MAX_RESEARCH_CLUSTERS)
        emit_event(
            "tool_result",
            {
                "record_count": len(academic_records),
                "cluster_count": len(research_clusters),
                "extraction": academic_extraction_meta,
            },
            stage="research_clustering",
            source="system",
        )

        if not research_clusters:
            if not vocabulary_task.done():
                vocabulary_task.cancel()
            return self._insufficient_result(
                topic=topic,
                scope=scope,
                scholar=scholar,
                scholar_queries=scholar_queries,
                query_generations=query_generations,
                academic_records=academic_records,
            )

        vocabulary = await vocabulary_task
        query_specs = self._build_adoption_query_specs(topic, vocabulary, adoption_queries)
        adoption = await self._run_adoption_scout(query_specs, max_results=max_results)
        adoption_items = _tag_items(_flatten_results(adoption), "adoption")
        adoption_records, adoption_extraction_meta = await self._extract_adoption_records(adoption_items)
        adoption_records = deduplicate_records(adoption_records)
        adoption_clusters = build_adoption_clusters(adoption_records, limit=MAX_ADOPTION_CLUSTERS)
        emit_event(
            "tool_result",
            {
                "record_count": len(adoption_records),
                "cluster_count": len(adoption_clusters),
                "extraction": adoption_extraction_meta,
            },
            stage="adoption_clustering",
            source="system",
        )

        links = await self._run_cluster_linkage(research_clusters, adoption_clusters)
        coverage = self._coverage(
            academic_records=academic_records,
            adoption_records=adoption_records,
            scholar_items=scholar_items,
            adoption_items=adoption_items,
            query_family_count=_query_family_count(vocabulary.query_families, query_specs),
            mapping_confidence=vocabulary.mapping_confidence,
            structured_record_count=len(academic_records) + len(adoption_records),
            total_relevant_results=academic_extraction_meta["total_relevant_results"] + adoption_extraction_meta["total_relevant_results"],
            adversarial=None,
        )
        evidence_floor = _topic_evidence_floor(academic_records, scholar_items)
        adoption_floor = _topic_adoption_floor(adoption_records)
        analyses = self._evaluate_all(
            research_clusters,
            adoption_clusters,
            links,
            coverage,
            scope.status == "unconfirmed",
            evidence_floor=evidence_floor,
            adoption_floor=adoption_floor,
        )
        top_candidate = _top_analysis(analyses)
        self._remember_partial(
            topic=topic,
            scope=scope,
            scholar=scholar,
            adoption=adoption,
            scholar_queries=scholar_queries,
            adoption_query_specs=query_specs,
            counter_query="",
            vocabulary=vocabulary,
            academic_records=academic_records,
            adoption_records=adoption_records,
            research_clusters=research_clusters,
            adoption_clusters=adoption_clusters,
            links=links,
            analyses=analyses,
            top_candidate=top_candidate,
            counter_evidence=[],
            reason="학술·산업 근거를 확보하고 1차 점수까지 계산했습니다.",
        )

        run_counter, counter_reason = self._should_run_adversarial_verifier(
            top_candidate=top_candidate,
            adoption_records=adoption_records,
            links=links,
        )
        counter_query = self._counter_query(topic, top_candidate) if run_counter else ""
        if run_counter:
            counter_result = await self._run_adversarial_verifier(counter_query)
            counter_items = _extract_counter_items(counter_result)
            counter_evidence = _extract_counter_evidence(counter_result)
            counter_records, counter_meta = await self._extract_adoption_records(counter_items)
        else:
            emit_event("note", {"text": counter_reason}, stage="adversarial_verifier", source="system")
            counter_result = {"events": [], "timed_out": False, "skipped": True, "reason": counter_reason}
            counter_items = []
            counter_evidence = []
            counter_records = []
            counter_meta = {"total_relevant_results": 0, "structured_record_count": 0, "failed_count": 0, "skipped": True}
        if counter_records:
            adoption_records = deduplicate_records([*adoption_records, *counter_records])
            adoption_clusters = build_adoption_clusters(adoption_records, limit=MAX_ADOPTION_CLUSTERS)
            refreshed = await self._run_cluster_linkage(
                [cluster for cluster in research_clusters if cluster.cluster_id == top_candidate.get("research_cluster_id")],
                adoption_clusters,
                timeout_stage="counter_relink",
            )
            links = [link for link in links if link.research_cluster_id != top_candidate.get("research_cluster_id")] + refreshed

        coverage = self._coverage(
            academic_records=academic_records,
            adoption_records=adoption_records,
            scholar_items=scholar_items,
            adoption_items=[*adoption_items, *counter_items],
            query_family_count=_query_family_count(vocabulary.query_families, query_specs),
            mapping_confidence=vocabulary.mapping_confidence,
            structured_record_count=len(academic_records) + len(adoption_records),
            total_relevant_results=academic_extraction_meta["total_relevant_results"] + adoption_extraction_meta["total_relevant_results"] + counter_meta["total_relevant_results"],
            adversarial={
                "performed": run_counter,
                "skipped_with_adoption": bool(counter_result.get("skipped")),
                "timed_out": _agent_search_failed(counter_result),
                "result_count": len({item.get("url") or item.get("title") for item in counter_items if item.get("url") or item.get("title")}),
            },
        )
        evidence_floor = _topic_evidence_floor(academic_records, scholar_items)
        adoption_floor = _topic_adoption_floor(adoption_records)
        analyses = self._evaluate_all(
            research_clusters,
            adoption_clusters,
            links,
            coverage,
            False,
            evidence_floor=evidence_floor,
            adoption_floor=adoption_floor,
        )
        top_candidate = _top_analysis(analyses)
        self._remember_partial(
            topic=topic,
            scope=scope,
            scholar=scholar,
            adoption=adoption,
            scholar_queries=scholar_queries,
            adoption_query_specs=query_specs,
            counter_query=counter_query,
            vocabulary=vocabulary,
            academic_records=academic_records,
            adoption_records=adoption_records,
            research_clusters=research_clusters,
            adoption_clusters=adoption_clusters,
            links=links,
            analyses=analyses,
            top_candidate=top_candidate,
            counter_evidence=counter_evidence,
            reason="반증 검색까지 반영한 잠정 판정입니다." if run_counter else counter_reason,
        )

        deep_target_id = top_candidate.get("research_cluster_id")
        deep_research = await self._run_conditional_deep_research(topic, top_candidate, counter_evidence)
        deep_items = deep_research.pop("adoption_items", [])
        deep_record_dicts = deep_research.pop("adoption_records", [])
        if deep_record_dicts:
            deep_records = [AdoptionEvidenceRecord.model_validate(item) for item in deep_record_dicts]
            adoption_records = deduplicate_records([*adoption_records, *deep_records])
            adoption_items = [*adoption_items, *deep_items]
            adoption_clusters = build_adoption_clusters(adoption_records, limit=MAX_ADOPTION_CLUSTERS)
            refreshed = await self._run_cluster_linkage(
                [cluster for cluster in research_clusters if cluster.cluster_id == deep_target_id],
                adoption_clusters,
                timeout_stage="counter_relink",
            )
            links = [link for link in links if link.research_cluster_id != deep_target_id] + refreshed
            coverage = self._coverage(
                academic_records=academic_records,
                adoption_records=adoption_records,
                scholar_items=scholar_items,
                adoption_items=[*adoption_items, *counter_items],
                query_family_count=_query_family_count(vocabulary.query_families, query_specs),
                mapping_confidence=vocabulary.mapping_confidence,
                structured_record_count=len(academic_records) + len(adoption_records),
                total_relevant_results=academic_extraction_meta["total_relevant_results"] + adoption_extraction_meta["total_relevant_results"] + counter_meta["total_relevant_results"] + len(deep_items),
                adversarial={
                    "performed": run_counter,
                    "skipped_with_adoption": bool(counter_result.get("skipped")),
                    "timed_out": _agent_search_failed(counter_result),
                    "result_count": len({item.get("url") or item.get("title") for item in [*counter_items, *deep_items] if item.get("url") or item.get("title")}),
                },
            )
            evidence_floor = _topic_evidence_floor(academic_records, scholar_items)
            adoption_floor = _topic_adoption_floor(adoption_records)
            analyses = self._evaluate_all(
                research_clusters,
                adoption_clusters,
                links,
                coverage,
                False,
                evidence_floor=evidence_floor,
                adoption_floor=adoption_floor,
            )
            top_candidate = next((item for item in analyses if item["research_cluster_id"] == deep_target_id), _top_analysis(analyses))
        deep_review = deep_research.get("review", {})
        if deep_review.get("explicit_outcome_mismatch"):
            top_candidate.setdefault("gap_types", []).append("outcome_gap")
            top_candidate["gap_types"] = list(dict.fromkeys(top_candidate["gap_types"]))
        top_candidate["confirmed_barriers"] = list(dict.fromkeys(deep_review.get("confirmed_barriers", []) + top_candidate.get("confirmed_barriers", [])))
        top_candidate["inferred_barriers"] = list(dict.fromkeys(deep_review.get("inferred_barriers", []) + top_candidate.get("inferred_barriers", [])))

        narrative_task = asyncio.create_task(self._run_gap_narrative(top_candidate, research_clusters, adoption_clusters))
        visualization_task = asyncio.create_task(self._run_gap_map(topic, top_candidate, max_results=max_results))
        narrative, visualization = await asyncio.gather(narrative_task, visualization_task)
        top_candidate.update(narrative)
        self._remember_partial(
            topic=topic,
            scope=scope,
            scholar=scholar,
            adoption=adoption,
            scholar_queries=scholar_queries,
            adoption_query_specs=query_specs,
            counter_query=counter_query,
            vocabulary=vocabulary,
            academic_records=academic_records,
            adoption_records=adoption_records,
            research_clusters=research_clusters,
            adoption_clusters=adoption_clusters,
            links=links,
            analyses=analyses,
            top_candidate=top_candidate,
            counter_evidence=counter_evidence,
            deep_research=deep_research,
            visualization=visualization,
            reason="시간 예산 안에서 확보된 근거로 잠정 판정했습니다.",
        )
        result = self._build_response(
            topic=topic,
            scope=scope,
            scholar=scholar,
            adoption=adoption,
            scholar_queries=scholar_queries,
            adoption_query_specs=query_specs,
            counter_query=counter_query,
            vocabulary=vocabulary,
            academic_records=academic_records,
            adoption_records=adoption_records,
            research_clusters=research_clusters,
            adoption_clusters=adoption_clusters,
            links=links,
            analyses=analyses,
            top_candidate=top_candidate,
            counter_evidence=counter_evidence,
            deep_research=deep_research,
            visualization=visualization,
            query_generations=query_generations,
        )
        result["analysis_status"] = "complete"
        emit_event(
            "finish",
            {
                "topic": topic,
                "label": result["label"],
                "scores": result["scores"],
                "research_clusters": len(research_clusters),
                "adoption_clusters": len(adoption_clusters),
                "counter_evidence": len(counter_evidence),
            },
            stage="finalization",
            source="system",
        )
        return result

    async def _calibrate_scope(self, topic: str):
        emit_event("note", {"text": "Scope Calibrator: 입력 주제의 범위를 판정합니다."}, stage="scope_calibrator", source="system")
        try:
            return await self.agents.scope(topic, timeout_s=self._stage_timeout("scope"))
        except AgentBudgetTimeout:
            emit_event("note", {"text": "Scope Calibrator 시간 예산이 끝나 원문 주제를 대표 범위로 사용합니다."}, stage="scope_calibrator", source="system")
            return ScopeDecision(status="focused", selected_topics=[topic], rationale="시간 예산 내 자동 범위 보정")

    async def _build_scholar_queries(self, topic: str, scope: Any, scholar_query: str | None) -> tuple[list[str], list[dict[str, Any]]]:
        if scholar_query:
            return [scholar_query], [{"query": scholar_query, "rationale": "디버깅용 scholar_query를 그대로 사용합니다."}]
        emit_event(
            "note",
            {"text": "Scope 결과를 학술 표준 용어와 배포 맥락이 포함된 Scholar 쿼리로 정제합니다."},
            stage="scholar_scout",
            source="system",
        )
        generated = []
        # One focused Scholar query is enough for the live demo. Running all
        # three scope suggestions concurrently caused Liner 429 responses and
        # pulled broad, irrelevant results into the extraction prompt.
        selected_topics = scope.selected_topics[:1] or [topic]
        tasks = [
            asyncio.create_task(
                self.agents.scholar_query(selected_topic, scope, timeout_s=self._stage_timeout("query_generation"))
            )
            for selected_topic in selected_topics
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for selected_topic, result in zip(selected_topics, results):
            if isinstance(result, AgentBudgetTimeout):
                emit_event("note", {"text": f"'{selected_topic}' 쿼리 생성이 늦어 원문 기술명을 검색어로 사용합니다."}, stage="scholar_scout", source="system")
                generated.append({"query": selected_topic, "rationale": "쿼리 생성 시간 예산 초과로 원문 사용"})
            elif isinstance(result, Exception):
                raise result
            else:
                generated.append(result.model_dump())
        return [item["query"] for item in generated], generated

    async def _run_scholar_scout(self, queries: list[str], *, max_results: int) -> dict[str, Any]:
        emit_event(
            "note",
            {"text": f"Scholar Scout: 정제된 학술 쿼리 {len(queries)}개로 검색합니다."},
            stage="scholar_scout",
            source="system",
        )
        tasks = [
            asyncio.create_task(
                self.liner.search_scholar(
                    query,
                    lang="ko",
                    max_results=max_results,
                    stage="scholar_scout",
                    timeout_s=self._stage_timeout("scholar_search"),
                )
            )
            for query in queries[:3]
        ]
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        normalized = []
        for response in responses:
            if isinstance(response, Exception):
                emit_event("error", {"name": "scholar_search", "message": str(response)}, stage="scholar_scout", source="liner")
                normalized.append({"results": [], "totalCount": 0, "error": str(response)})
            else:
                normalized.append(response)
        return _merge_search_results(normalized)

    async def _run_vocabulary_bridge(self, topic: str, scholar: dict[str, Any]):
        emit_event("note", {"text": "Vocabulary Bridge: 학술 용어를 산업 검색어와 세 검색 관점으로 변환합니다."}, stage="vocabulary_bridge", source="system")
        try:
            return await self.agents.vocabulary_bridge(topic, scholar, timeout_s=self._stage_timeout("academic_vocab"))
        except AgentBudgetTimeout:
            emit_event("note", {"text": "Vocabulary Bridge 시간 예산이 끝나 학술 주제를 산업 검색어로 그대로 연결합니다."}, stage="vocabulary_bridge", source="system")
            return VocabularyBridgeResult(
                terms=[topic],
                query_families=QueryFamilies(technology=[topic]),
                mapping_confidence=0,
                rationale="용어 변환 시간 예산 초과로 입력 주제를 사용했습니다.",
            )

    def _build_adoption_query_specs(self, topic: str, vocabulary: Any, overrides: list[str] | None) -> list[tuple[str, str]]:
        if overrides:
            return [(query, "manual") for query in overrides[: self.runtime.max_adoption_queries]]
        families = vocabulary.query_families.model_dump()
        if not any(families.values()):
            families = {"technology": vocabulary.terms, "use_case": [], "context": []}

        technologies = _clean_terms(families.get("technology", [])) or _clean_terms(vocabulary.terms)
        use_cases = _clean_terms(families.get("use_case", []))
        contexts = _clean_terms(families.get("context", []))
        korean_input = _contains_hangul(" ".join([*technologies, *use_cases, *contexts, *map(str, vocabulary.terms or [])]))

        specs: list[tuple[str, str]] = []
        topic = str(topic or "").strip()
        if topic:
            if _contains_hangul(topic):
                specs.append((f"{topic} 산업 적용 사례", "topic"))
                specs.append((f"{topic} 상용 서비스", "topic"))
            specs.append((f"{topic} production deployment", "topic"))
            specs.append((f"{topic} inference serving production", "topic"))
        if korean_input and technologies and use_cases:
            specs.append((f"{technologies[0]} {use_cases[0]} 산업 적용 사례", "technology+use_case"))
            specs.append((f"{technologies[0]} {use_cases[0]} 상용 서비스", "technology+use_case"))
        if korean_input and technologies and contexts:
            specs.append((f"{technologies[0]} {contexts[0]} 현장 적용", "technology+context"))
        if technologies and use_cases:
            specs.append((f"{technologies[0]} {use_cases[0]} production deployment", "technology+use_case"))
        if technologies and len(use_cases) > 1:
            specs.append((f"{technologies[0]} {use_cases[1]} production deployment", "technology+use_case"))
        if technologies and contexts:
            specs.append((f"{technologies[0]} {contexts[0]} engineering blog production", "technology+context"))
        if technologies:
            specs.append((f"{technologies[0]} inference serving production", "technology"))
            specs.append((f"{technologies[0]} cloud deployment case study", "technology"))
        for technology in technologies[:2]:
            for use_case in use_cases[:2]:
                specs.append((f"{technology} {use_case} case study", "technology+use_case"))
        for use_case in use_cases[:2]:
            for context in contexts[:2]:
                specs.append((f"{use_case} {context} customer deployment", "use_case+context"))
        for technology in technologies[:2]:
            for context in contexts[:2]:
                specs.append((f"{technology} {context} real-world implementation", "technology+context"))

        if not specs:
            if korean_input:
                specs = [(f"{term} 도입 사례 현장 적용", "technology") for term in _clean_terms(vocabulary.terms)]
            else:
                specs = [(f"{term} production deployment case study", "technology") for term in _clean_terms(vocabulary.terms)]
        if not specs:
            specs = [(term, "technology") for term in _clean_terms(families.get("technology", []))]
        return list(dict.fromkeys(specs))[: self.runtime.max_adoption_queries]

    async def _run_adoption_scout(self, query_specs: list[tuple[str, str]], *, max_results: int) -> list[dict[str, Any]]:
        emit_event(
            "note",
            {"text": f"Adoption Scout: 산업 검색어 {len(query_specs)}개를 세 검색 관점으로 확인합니다."},
            stage="adoption_scout",
            source="system",
        )
        async def search_one(query: str, family: str) -> dict[str, Any]:
            response = await self.liner.search_web(
                query,
                lang="ko",
                max_results=max_results,
                stage="adoption_scout",
                timeout_s=self._stage_timeout("adoption_search"),
            )
            tagged = dict(response)
            tagged["query"] = query
            tagged["query_family"] = family
            tagged["results"] = [{**_tag_item(item, family), "query": query} for item in response.get("results", [])]
            return tagged

        responses = await asyncio.gather(
            *(search_one(query, family) for query, family in query_specs[: self.runtime.max_adoption_queries]),
            return_exceptions=True,
        )
        normalized = []
        for response in responses:
            if isinstance(response, Exception):
                emit_event("error", {"name": "adoption_search", "message": str(response)}, stage="adoption_scout", source="liner")
                normalized.append({"results": [], "totalCount": 0, "error": str(response)})
            else:
                normalized.append(response)
        return normalized

    async def _extract_academic_records(self, items: list[dict[str, Any]]) -> tuple[list[AcademicEvidenceRecord], dict[str, Any]]:
        emit_event("note", {"text": f"Scholar 결과 {len(items)}건에서 학술 적용 주장과 검증 신호를 구조화합니다."}, stage="academic_extraction", source="system")
        if not items:
            return [], {"total_relevant_results": 0, "structured_record_count": 0, "failed_count": 0}
        source_items = self._limit_extraction_items(items, stage="academic_extraction")
        try:
            batch: AcademicExtractionBatch = await self.agents.academic_extract(source_items, timeout_s=self._stage_timeout("academic_extraction"))
        except AgentBudgetTimeout:
            fallback_records = _fallback_academic_records(source_items, set())
            emit_event(
                "note",
                {"text": f"학술 근거 구조화 시간 예산 초과 → Scholar 제목 기반 보조 근거 {len(fallback_records)}건을 반영합니다."},
                stage="academic_extraction",
                source="system",
            )
            return fallback_records, {
                "total_relevant_results": len(items),
                "structured_record_count": len(fallback_records),
                "failed_count": max(0, len(items) - len(fallback_records)),
                "timed_out": True,
                "fallback_count": len(fallback_records),
            }
        except Exception as exc:
            fallback_records = _fallback_academic_records(source_items, set())
            emit_event(
                "note",
                {"text": f"학술 근거 구조화 실패 → Scholar 제목 기반 보조 근거 {len(fallback_records)}건을 반영합니다.", "error": str(exc)},
                stage="academic_extraction",
                source="system",
            )
            return fallback_records, {
                "total_relevant_results": len(items),
                "structured_record_count": len(fallback_records),
                "failed_count": max(0, len(items) - len(fallback_records)),
                "error": str(exc),
                "fallback_count": len(fallback_records),
            }
        records: list[AcademicEvidenceRecord] = []
        for extraction in batch.records:
            if not extraction.is_relevant or extraction.extraction_confidence < 0.55:
                continue
            item = source_items[extraction.source_index] if extraction.source_index < len(source_items) else None
            if not item or not extraction.evidence_span or not extraction.technology_canonical:
                continue
            records.append(
                AcademicEvidenceRecord(
                    record_id=stable_id("acad", source_id(item), extraction.evidence_span),
                    source_id=source_id(item),
                    source_url=item.get("url") or item.get("link") or "",
                    source_title=item.get("title") or "제목 없음",
                    published_at=item.get("published_at") or item.get("publishedAt"),
                    citation_count=item.get("citationCount"),
                    technology_raw=extraction.technology_raw,
                    technology_canonical=normalize_text(extraction.technology_canonical) or None,
                    use_case_raw=extraction.use_case_raw,
                    use_case_canonical=normalize_text(extraction.use_case_canonical) or None,
                    context_raw=extraction.context_raw,
                    context_canonical=normalize_text(extraction.context_canonical) or None,
                    expected_value_raw=extraction.expected_value_raw,
                    expected_value_canonical=normalize_text(extraction.expected_value_canonical) or None,
                    canonical_claim=extraction.canonical_claim or extraction.evidence_span,
                    evidence_span=extraction.evidence_span,
                    extraction_confidence=extraction.extraction_confidence,
                    query_family=item.get("query_family"),
                    is_replication=extraction.is_replication,
                    is_synthesis=extraction.is_synthesis,
                    is_real_world=extraction.is_real_world,
                    is_counter_evidence=extraction.is_counter_evidence,
                    result_direction=extraction.result_direction,
                    institutions=extraction.institutions,
                )
            )
        fallback_records = _fallback_academic_records(source_items, {record.source_id for record in records})
        if fallback_records and len(records) < min(3, len(source_items)):
            emit_event(
                "note",
                {"text": f"구조화되지 않은 Scholar 결과 {len(fallback_records)}건을 제목 기반 보조 근거로 반영합니다."},
                stage="academic_extraction",
                source="system",
            )
            records.extend(fallback_records)
        return records, {
            "total_relevant_results": len(items),
            "structured_record_count": len(records),
            "failed_count": max(0, len(items) - len(records)),
            "fallback_count": len(fallback_records),
            "structured_source_count": len(source_items),
        }

    async def _extract_adoption_records(self, items: list[dict[str, Any]]) -> tuple[list[AdoptionEvidenceRecord], dict[str, Any]]:
        emit_event("note", {"text": f"산업 검색 결과 {len(items)}건에서 실제 도입·중단 사건을 구조화합니다."}, stage="adoption_extraction", source="system")
        if not items:
            return [], {"total_relevant_results": 0, "structured_record_count": 0, "failed_count": 0}
        source_items = self._limit_extraction_items(items, stage="adoption_extraction")
        try:
            batch: AdoptionExtractionBatch = await self.agents.adoption_extract(source_items, timeout_s=self._stage_timeout("adoption_extraction"))
        except AgentBudgetTimeout:
            fallback_records = _fallback_adoption_records(source_items, set())
            if fallback_records:
                emit_event(
                    "note",
                    {"text": f"산업 도입 근거 구조화 시간 예산 초과 → 명시적 운영 신호 {len(fallback_records)}건을 보조 근거로 반영합니다."},
                    stage="adoption_extraction",
                    source="system",
                )
            else:
                emit_event("note", {"text": "산업 도입 근거 구조화 시간 예산이 끝나 확인된 기록만으로 잠정 판정합니다."}, stage="adoption_extraction", source="system")
            return fallback_records, {
                "total_relevant_results": len(items),
                "structured_record_count": len(fallback_records),
                "failed_count": max(0, len(items) - len(fallback_records)),
                "timed_out": True,
                "fallback_count": len(fallback_records),
            }
        except Exception as exc:
            fallback_records = _fallback_adoption_records(source_items, set())
            emit_event(
                "note",
                {"text": f"산업 도입 근거 구조화 실패 → 명시적 운영 신호 {len(fallback_records)}건을 보조 근거로 반영합니다.", "error": str(exc)},
                stage="adoption_extraction",
                source="system",
            )
            return fallback_records, {
                "total_relevant_results": len(items),
                "structured_record_count": len(fallback_records),
                "failed_count": max(0, len(items) - len(fallback_records)),
                "error": str(exc),
                "fallback_count": len(fallback_records),
            }
        records: list[AdoptionEvidenceRecord] = []
        rejected_count = 0
        for extraction in batch.records:
            if not extraction.is_relevant or extraction.extraction_confidence < 0.55:
                rejected_count += 1
                continue
            item = source_items[extraction.source_index] if extraction.source_index < len(source_items) else None
            technology_canonical = normalize_text(extraction.technology_canonical or extraction.technology_raw) or None
            subject_canonical = normalize_text(extraction.subject_canonical or extraction.subject_raw) or None
            if not item or not extraction.evidence_span or not technology_canonical or not subject_canonical:
                rejected_count += 1
                continue
            claim_text = " ".join(
                str(value or "")
                for value in (
                    item.get("title"),
                    item.get("snippet"),
                    extraction.evidence_span,
                    extraction.technology_raw,
                    extraction.use_case_raw,
                    extraction.context_raw,
                )
            )
            if extraction.relation == "uses" and not _has_query_overlap(item, claim_text):
                rejected_count += 1
                continue
            if extraction.relation == "uses" and _subject_overlaps_query(subject_canonical, item):
                rejected_count += 1
                continue
            if extraction.relation == "uses" and _subject_looks_like_title_fragment(subject_canonical, item):
                rejected_count += 1
                continue
            if extraction.relation == "uses" and not _looks_like_industry_adoption_result(item):
                rejected_count += 1
                continue
            if not _has_actionable_adoption_locus(extraction):
                rejected_count += 1
                continue
            usage_context = extraction.usage_context if extraction.relation == "uses" else None
            adoption_stage = extraction.adoption_stage if extraction.relation == "uses" and usage_context != "vendor_product_integration" else None
            records.append(
                AdoptionEvidenceRecord(
                    record_id=stable_id("adopt", source_id(item), extraction.evidence_span),
                    source_id=source_id(item),
                    source_url=item.get("url") or item.get("link") or "",
                    source_title=item.get("title") or "제목 없음",
                    published_at=item.get("published_at") or item.get("publishedAt"),
                    technology_raw=extraction.technology_raw,
                    technology_canonical=technology_canonical,
                    use_case_raw=extraction.use_case_raw,
                    use_case_canonical=normalize_text(extraction.use_case_canonical or extraction.use_case_raw) or None,
                    context_raw=extraction.context_raw,
                    context_canonical=normalize_text(extraction.context_canonical or extraction.context_raw) or None,
                    expected_value_raw=extraction.expected_value_raw,
                    expected_value_canonical=normalize_text(extraction.expected_value_canonical or extraction.expected_value_raw) or None,
                    canonical_claim=extraction.canonical_claim or extraction.evidence_span,
                    evidence_span=extraction.evidence_span,
                    extraction_confidence=extraction.extraction_confidence,
                    query_family=item.get("query_family"),
                    subject_raw=extraction.subject_raw,
                    subject_canonical=subject_canonical,
                    relation=extraction.relation,
                    usage_context=usage_context,
                    adoption_stage=adoption_stage,
                    deployment_unit=extraction.deployment_unit,
                    project_name=extraction.project_name,
                    event_date=extraction.event_date,
                    explicit_barriers=extraction.explicit_barriers,
                )
            )
        fallback_records = _fallback_adoption_records(source_items, {record.source_id for record in records})
        if fallback_records:
            emit_event(
                "note",
                {"text": f"명시적 운영 신호가 있는 산업 검색 결과 {len(fallback_records)}건을 보조 구조화 근거로 반영합니다."},
                stage="adoption_extraction",
                source="system",
            )
            records.extend(fallback_records)
        return records, {
            "total_relevant_results": len(items),
            "structured_record_count": len(records),
            "failed_count": max(0, len(items) - len(records)),
            "rejected_weak_adoption_count": rejected_count,
            "fallback_count": len(fallback_records),
            "structured_source_count": len(source_items),
        }

    def _limit_extraction_items(self, items: list[dict[str, Any]], *, stage: str) -> list[dict[str, Any]]:
        limit = self.runtime.max_extraction_items
        if len(items) <= limit:
            return items
        emit_event(
            "note",
            {
                "text": f"구조화 대상이 {len(items)}건이라 상위 {limit}건만 사용합니다.",
                "total_count": len(items),
                "structured_source_count": limit,
            },
            stage=stage,
            source="system",
        )
        return items[:limit]

    async def _run_cluster_linkage(
        self,
        research_clusters: list[ResearchCluster],
        adoption_clusters: list[AdoptionCluster],
        *,
        timeout_stage: str = "linkage",
    ) -> list[ClusterLink]:
        emit_event(
            "note",
            {"text": f"연구 클러스터 {len(research_clusters)}개와 산업 클러스터 {len(adoption_clusters)}개를 네 차원으로 비교합니다."},
            stage="cluster_linkage",
            source="system",
        )
        if not research_clusters:
            return []
        pairs = []
        for research in research_clusters:
            for adoption in adoption_clusters[:MAX_LINK_CANDIDATES_PER_RESEARCH]:
                pairs.append({"research": _cluster_summary(research), "adoption": _cluster_summary(adoption)})
        dimensions = {}
        if pairs:
            try:
                result = await self.agents.cluster_link(pairs, timeout_s=self._stage_timeout(timeout_stage))
                dimensions = {(item.research_cluster_id, item.adoption_cluster_id): item for item in result.links}
            except AgentBudgetTimeout:
                emit_event("note", {"text": "클러스터 연결 판단 시간 예산이 끝나 텍스트 유사도와 검색 범위만으로 연결을 계산합니다."}, stage="cluster_linkage", source="system")
        adoption_by_id = {cluster.cluster_id: cluster for cluster in adoption_clusters}
        links: list[ClusterLink] = []
        for research in research_clusters:
            research_links = []
            for adoption in adoption_clusters[:MAX_LINK_CANDIDATES_PER_RESEARCH]:
                item = dimensions.get((research.cluster_id, adoption.cluster_id))
                if item:
                    dimension_values = {
                        "technology": item.technology_match,
                        "use_case": item.use_case_match,
                        "context": item.context_match,
                        "expected_value": item.expected_value_match,
                    }
                    link = make_cluster_link(
                        research,
                        adoption,
                        dimension_values,
                        explanation=item.explanation,
                        confidence=item.confidence,
                        matched_on=item.matched_on,
                        missing_on=item.missing_on,
                    )
                else:
                    link = make_cluster_link(research, adoption)
                if link.link_type != "unlinked" or link.link_similarity >= 0.30:
                    research_links.append(link)
            if not research_links or not any(link.link_type in {"direct", "partial", "blocked"} for link in research_links):
                research_links.append(make_cluster_link(research, None, explanation="검색 범위 안에서 유효한 산업 연결을 확인하지 못했습니다."))
            links.extend(research_links)
        emit_event(
            "tool_result",
            {"link_count": len(links), "direct": sum(link.link_type == "direct" for link in links), "partial": sum(link.link_type == "partial" for link in links), "blocked": sum(link.link_type == "blocked" for link in links)},
            stage="cluster_linkage",
            source="system",
        )
        return links

    def _stage_timeout(self, stage: str, *, reserve_s: float | None = None) -> float | None:
        if self.runtime.disable_timeouts:
            return None
        configured = {
            "scope": self.runtime.scope_timeout_s,
            "query_generation": self.runtime.query_generation_timeout_s,
            "scholar_search": self.runtime.scholar_search_timeout_s,
            "academic_vocab": self.runtime.academic_vocab_timeout_s,
            "adoption_search": self.runtime.adoption_search_timeout_s,
            "academic_extraction": self.runtime.academic_extraction_timeout_s,
            "adoption_extraction": self.runtime.adoption_extraction_timeout_s,
            "linkage": self.runtime.linkage_timeout_s,
            "counter_relink": self.runtime.counter_relink_timeout_s,
            "adversarial": self.runtime.adversarial_timeout_s,
            "deep_research": self.runtime.deep_research_timeout_s,
            "finalization": self.runtime.finalization_timeout_s,
            "visualization": self.runtime.visualization_timeout_s,
        }[stage]
        if reserve_s is None:
            reserve_s = self.runtime.final_reserve_s if stage not in {"finalization", "visualization"} else 0
        return self.deadline.timeout(configured, reserve_s=reserve_s)

    def _remember_partial(
        self,
        *,
        topic: str,
        scope: Any,
        scholar: dict[str, Any],
        adoption: list[dict[str, Any]],
        scholar_queries: list[str],
        adoption_query_specs: list[tuple[str, str]],
        counter_query: str,
        vocabulary: Any,
        academic_records: list[Any],
        adoption_records: list[Any],
        research_clusters: list[Any],
        adoption_clusters: list[Any],
        links: list[ClusterLink],
        analyses: list[dict[str, Any]],
        top_candidate: dict[str, Any],
        counter_evidence: list[dict[str, Any]],
        reason: str,
        deep_research: dict[str, Any] | None = None,
        visualization: dict[str, Any] | None = None,
    ) -> None:
        self.last_partial_result = self._build_response(
            topic=topic,
            scope=scope,
            scholar=scholar,
            adoption=adoption,
            scholar_queries=scholar_queries,
            adoption_query_specs=adoption_query_specs,
            counter_query=counter_query,
            vocabulary=vocabulary,
            academic_records=academic_records,
            adoption_records=adoption_records,
            research_clusters=research_clusters,
            adoption_clusters=adoption_clusters,
            links=links,
            analyses=analyses,
            top_candidate=top_candidate,
            counter_evidence=counter_evidence,
            deep_research=deep_research or {"used": False, "timed_out": False, "status": "not_started"},
            visualization=visualization or {"requested": False, "artifact_received": False},
            query_generations=[],
        )
        self.last_partial_result.update(
            {
                "analysis_status": "partial",
                "partial_reason": reason,
                "remaining_budget_s": round(self.deadline.remaining(), 2),
            }
        )

    def _coverage(self, *, academic_records: list[Any], adoption_records: list[Any], scholar_items: list[dict[str, Any]], adoption_items: list[dict[str, Any]], query_family_count: int, mapping_confidence: float, structured_record_count: int, total_relevant_results: int, adversarial: dict[str, Any] | None) -> dict[str, Any]:
        return calculate_coverage_confidence(
            unique_academic_sources=len({key for item in scholar_items if (key := item.get("url") or item.get("title"))}),
            unique_web_sources=len({key for item in adoption_items if (key := item.get("url") or item.get("title"))}),
            query_family_count=query_family_count,
            mapping_confidence=mapping_confidence,
            structured_record_count=structured_record_count,
            total_relevant_results=total_relevant_results,
            adversarial_performed=bool(adversarial and adversarial.get("performed")),
            adversarial_timed_out=bool(adversarial and adversarial.get("timed_out")),
            adversarial_result_count=int((adversarial or {}).get("result_count", 0)),
            adversarial_skipped_with_adoption=bool(adversarial and adversarial.get("skipped_with_adoption")),
        )

    def _evaluate_all(
        self,
        research_clusters: list[ResearchCluster],
        adoption_clusters: list[AdoptionCluster],
        links: list[ClusterLink],
        coverage: dict[str, Any],
        field_not_confirmed: bool,
        *,
        evidence_floor: int = 0,
        adoption_floor: int = 0,
    ) -> list[dict[str, Any]]:
        adoption_by_id = {cluster.cluster_id: cluster for cluster in adoption_clusters}
        by_research: dict[str, list[ClusterLink]] = {}
        for link in links:
            by_research.setdefault(link.research_cluster_id, []).append(link)
        analyses = []
        for research in research_clusters:
            cluster_links = by_research.get(research.cluster_id, [])
            adoption_breakdown = calculate_adoption_evidence(cluster_links, adoption_by_id)
            coverage_breakdown = coverage
            raw_maturity = research.evidence_maturity or 0
            raw_adoption_score = adoption_breakdown["total"]
            maturity = max(raw_maturity, evidence_floor)
            adoption_score = max(raw_adoption_score, adoption_floor)
            if evidence_floor > raw_maturity:
                research.evidence_maturity = maturity
            if adoption_floor > raw_adoption_score:
                adoption_breakdown["signals"]["topic_floor_applied"] = adoption_floor
                adoption_breakdown["details"]["raw_linked_total"] = raw_adoption_score
                adoption_breakdown["total"] = adoption_score
            coverage_score = coverage_breakdown["total"]
            gap_types = classify_gap_types(research, cluster_links, adoption_by_id, coverage_score)
            if adoption_score > 0:
                gap_types = [
                    gap_type
                    for gap_type in gap_types
                    if gap_type not in {"no_adoption_link", "possible_no_adoption_link"}
                ]
            direct_production = int(adoption_breakdown["signals"]["direct_production_orgs"])
            label = classify_final_label(
                field_not_confirmed=field_not_confirmed,
                evidence_maturity=maturity,
                adoption_evidence=adoption_score,
                coverage_confidence=coverage_score,
                direct_production_org_count=direct_production,
            )
            priority = calculate_gap_priority(maturity, adoption_score, coverage_score)
            deep, deep_reasons = should_deep_research(
                contradicts_count=research.contradicts_count,
                evidence_maturity=maturity,
                direct_links_exist=any(link.link_type == "direct" for link in cluster_links),
                blocked_links_exist=any(link.link_type == "blocked" for link in cluster_links),
                gap_priority=priority,
                coverage_confidence=coverage_score,
                high_impact_candidate=priority >= 45,
                barrier_reason_unknown=any(link.link_type == "blocked" for link in cluster_links) and not any(
                    adoption_by_id.get(link.adoption_cluster_id or "") and adoption_by_id[link.adoption_cluster_id].explicit_barriers
                    for link in cluster_links
                ),
            )
            evidence_signals = _maturity_signals(research)
            evidence_details = {}
            if evidence_floor > raw_maturity:
                evidence_signals["topic_floor_applied"] = evidence_floor
                evidence_details["raw_cluster_total"] = raw_maturity
            analyses.append(
                {
                    "research_cluster_id": research.cluster_id,
                    "research_cluster": research.model_dump(),
                    "scores": {"evidence_maturity": maturity, "adoption_evidence": adoption_score, "coverage_confidence": coverage_score, "gap_priority": priority},
                    "score_breakdown": {
                        "evidence_maturity": {"total": maturity, "signals": evidence_signals, "details": evidence_details},
                        "adoption_evidence": adoption_breakdown,
                        "coverage_confidence": coverage_breakdown,
                    },
                    "label": label,
                    "gap_types": gap_types,
                    "links": [link.model_dump() for link in cluster_links],
                    "candidate_connections": [candidate.model_dump() for candidate in build_candidate_connections(cluster_links)],
                    "confirmed_barriers": [barrier for link in cluster_links for barrier in adoption_by_id.get(link.adoption_cluster_id or "", AdoptionCluster(cluster_id="", subject="", technology="")).explicit_barriers if link.link_type == "blocked"],
                    "inferred_barriers": [],
                    "should_deep_research": deep,
                    "deep_research_reasons": deep_reasons,
                    "rationale": "",
                    "connected_points": [],
                    "gap_points": [],
                    "potential_points": [],
                }
            )
        emit_event(
            "tool_result",
            {"analysis_count": len(analyses), "labels": {label: sum(item["label"] == label for item in analyses) for label in {item["label"] for item in analyses}}},
            stage="score_calculation",
            source="system",
        )
        return analyses

    def _should_run_adversarial_verifier(
        self,
        *,
        top_candidate: dict[str, Any],
        adoption_records: list[Any],
        links: list[ClusterLink],
    ) -> tuple[bool, str]:
        research_id = top_candidate.get("research_cluster_id")
        candidate_links = [link for link in links if link.research_cluster_id == research_id]
        connected_links = [link for link in candidate_links if link.link_type in {"direct", "partial"}]
        blocked_links = [link for link in candidate_links if link.link_type == "blocked"]
        relations = {record.relation for record in adoption_records if getattr(record, "relation", None)}

        if "uses" in relations and "does_not_use" in relations:
            return True, "도입 증거와 중단·거절 증거가 함께 있어 반증 검색으로 모순을 확인합니다."
        if connected_links:
            return False, "1차 산업 검색에서 연결 가능한 도입 근거를 확인해 반증 검색을 생략합니다."
        if blocked_links:
            return True, "도입 중단·거절 근거만 확인되어 반증 검색으로 현재 도입 여부를 다시 확인합니다."
        if adoption_records:
            return True, "산업 도입 후보는 있으나 상위 연구 클러스터와 연결되지 않아 동일 사용 사례 도입 여부를 재확인합니다."
        return True, "1차 산업 검색에서 연결 가능한 도입 근거가 없어 반증 검색을 수행합니다."

    async def _run_adversarial_verifier(self, counter_query: str) -> dict[str, Any]:
        emit_event("note", {"text": "갭을 선언하기 전에 동일한 사용 사례·환경에서 이미 운영 중이라는 반증을 검색합니다."}, stage="adversarial_verifier", source="system")
        try:
            return await self.liner.search_agent(
                [{"role": "user", "content": counter_query}],
                mode="general",
                lang="ko",
                stage="adversarial_verifier",
                timeout_s=self._stage_timeout("adversarial"),
            )
        except AgentBudgetTimeout:
            emit_event("note", {"text": "반증 검색 시간 예산이 끝나 1차 근거를 기준으로 잠정 판정합니다."}, stage="adversarial_verifier", source="system")
            return {"events": [], "timed_out": True, "timeout_kind": "budget"}

    def _counter_query(self, topic: str, analysis: dict[str, Any]) -> str:
        cluster = analysis.get("research_cluster", {})
        return (
            f"Find direct evidence that {topic} is already used in production for the same technology "
            f"({cluster.get('technology')}), use case ({cluster.get('use_case')}), and context ({cluster.get('context')}). "
            "Prefer customer deployments, production case studies, or operator reports. Do not treat plans, compatibility, or job postings as production adoption."
        )

    async def _run_conditional_deep_research(self, topic: str, analysis: dict[str, Any], counter_evidence: list[dict[str, Any]]) -> dict[str, Any]:
        if not analysis.get("should_deep_research"):
            reason = "코드 계산 결과 충돌·중단·검색 부족에 해당하지 않아 Deep Research를 건너뜁니다."
            emit_event("note", {"text": reason}, stage="conditional_deep_research", source="system")
            return {"used": False, "timed_out": False, "status": "skipped", "reason": reason, "review": {}}
        reasons = analysis.get("deep_research_reasons", [])
        emit_event("note", {"text": f"{'; '.join(reasons)} → Deep Research로 조건부 승격합니다."}, stage="conditional_deep_research", source="system")
        timeout_s = self._stage_timeout("deep_research")
        research = await self.liner.deep_research(
            [{"role": "user", "content": f"Investigate the application gap for {topic}. Analyze this candidate: {analysis}. Counter-evidence: {counter_evidence}"}],
            lang="ko",
            timeout_s=timeout_s,
            stage="conditional_deep_research",
        )
        report = _extract_report(research)
        if research.get("timed_out"):
            reason = "Deep Research timeout → Search 근거로 잠정 결론을 유지하고 확신도를 낮춥니다."
            emit_event("note", {"text": reason}, stage="conditional_deep_research", source="system")
            return {"used": True, "timed_out": True, "status": "timeout", "reason": reason, "events_received": len(research.get("events", [])), "report": report, "review": {}, "adoption_items": [], "adoption_records": []}
        review = None
        if report:
            try:
                review = await self.agents.review_deep_research(report, timeout_s=self._stage_timeout("finalization"))
            except AgentBudgetTimeout:
                emit_event("note", {"text": "Deep Research 검토 시간 예산이 끝나 보고서 자체를 보조 근거로만 보관합니다."}, stage="conditional_deep_research", source="system")
        deep_items = _extract_counter_items(research)
        deep_records, _ = await self._extract_adoption_records(deep_items)
        return {
            "used": True,
            "timed_out": False,
            "status": "completed",
            "reason": "조건부 승격 완료",
            "events_received": len(research.get("events", [])),
            "report": report,
            "review": review.model_dump() if review else {},
            "adoption_items": deep_items,
            "adoption_records": [record.model_dump() for record in deep_records],
        }

    async def _run_gap_narrative(self, analysis: dict[str, Any], research_clusters: list[ResearchCluster], adoption_clusters: list[AdoptionCluster]) -> dict[str, Any]:
        # 점수와 label은 이미 결정론적으로 계산됐다. finalization agent는 그 값을
        # 바꾸지 않고, 메인 설명 흐름 뒤에 붙일 기회 제안만 보강한다.
        emit_event(
            "note",
            {"text": "최종 설명은 계산된 점수·연결·갭 유형을 바탕으로 정리합니다."},
            stage="finalization",
            source="system",
        )
        deterministic = _deterministic_narrative(analysis)
        deterministic["opportunity_suggestions"] = []
        if not _can_suggest_opportunities(analysis, deterministic):
            return deterministic

        emit_event(
            "note",
            {"text": "점수·라벨은 유지하고 Gap Narrator로 실행 가능한 기회 제안만 보강합니다."},
            stage="finalization",
            source="system",
        )
        try:
            model_narrative = await self.agents.gap_narrative(
                _narrative_agent_payload(analysis, deterministic, research_clusters, adoption_clusters),
                timeout_s=self._stage_timeout("finalization"),
            )
            suggestions = _clean_opportunity_suggestions(model_narrative.opportunity_suggestions)
            if suggestions:
                deterministic["opportunity_suggestions"] = suggestions
                return deterministic
            emit_event(
                "note",
                {"text": "Gap Narrator가 기회 제안을 비워 반환해 계산된 갭 타입으로 보수적 제안을 생성합니다."},
                stage="finalization",
                source="system",
            )
        except AgentBudgetTimeout:
            emit_event(
                "note",
                {"text": "Gap Narrator 시간 예산이 끝나 계산된 갭 타입으로 기회 제안을 생성합니다."},
                stage="finalization",
                source="system",
            )
        except Exception as exc:
            emit_event(
                "note",
                {"text": "Gap Narrator 실패 → 계산된 갭 타입으로 기회 제안을 생성합니다.", "error": str(exc)},
                stage="finalization",
                source="system",
            )
        deterministic["opportunity_suggestions"] = _deterministic_opportunity_suggestions(analysis, deterministic)
        return deterministic

    async def _run_gap_map(self, topic: str, analysis: dict[str, Any], *, max_results: int) -> dict[str, Any]:
        emit_event("note", {"text": "최종 점수와 연구·산업 연결 구조를 Gap Map 시각화로 전달합니다."}, stage="gap_map", source="system")
        scores = analysis.get("scores", {})
        query = f"Application gap comparison for {topic}: {scores}. Label: {analysis.get('label')}. Gap types: {analysis.get('gap_types', [])}."
        visualization = await self.liner.visualize(
            query,
            is_search_context=True,
            max_results=max_results,
            appearance="light",
            stage="gap_map",
            timeout_s=self._stage_timeout("visualization", reserve_s=0),
        )
        return {"requested": True, "artifact_received": _has_event_type(visualization, "data-atlas"), "events_received": len(visualization.get("events", []))}

    def _insufficient_result(self, *, topic: str, scope: Any, scholar: dict[str, Any], scholar_queries: list[str], query_generations: list[dict[str, Any]], academic_records: list[Any]) -> dict[str, Any]:
        reason = "학술 검색 결과에서 점수 계산에 필요한 적용 주장을 구조화하지 못했습니다."
        emit_event("note", {"text": reason}, stage="finalization", source="system")
        result = {
            "topic": topic,
            "scope": scope.model_dump(),
            "queries": {"scholar": scholar_queries, "adoption": [], "counter": ""},
            "scores": {"evidence_maturity": 0, "adoption_evidence": 0, "coverage_confidence": 0, "gap_priority": 0},
            "label": "insufficient_evidence",
            "gap_types": ["possible_no_adoption_link"],
            "rationale": reason,
            "connected_points": [],
            "gap_points": [],
            "potential_points": [],
            "confirmed_barriers": [],
            "inferred_barriers": [],
            "evidence": scholar.get("results", []),
            "counter_evidence": [],
            "deep_research": {"used": False, "timed_out": False, "status": "skipped", "reason": reason},
            "visualization": {"requested": False, "artifact_received": False},
            "academic_evidence": [record.model_dump() for record in academic_records],
            "adoption_evidence": [],
            "research_clusters": [],
            "adoption_clusters": [],
            "links": [],
            "gap_candidates": [],
            "scholar": scholar,
            "adoption": [],
            "scholar_query_generation": query_generations,
            "vocabulary": {"terms": [], "industry_terms": [], "query_families": {}, "mapping_confidence": 0, "rationale": ""},
            "gap_candidate": None,
        }
        emit_event("finish", {"topic": topic, "label": result["label"], "scores": result["scores"]}, stage="finalization", source="system")
        return result

    def _build_response(self, *, topic: str, scope: Any, scholar: dict[str, Any], adoption: list[dict[str, Any]], scholar_queries: list[str], adoption_query_specs: list[tuple[str, str]], counter_query: str, vocabulary: Any, academic_records: list[Any], adoption_records: list[Any], research_clusters: list[Any], adoption_clusters: list[Any], links: list[ClusterLink], analyses: list[dict[str, Any]], top_candidate: dict[str, Any], counter_evidence: list[dict[str, Any]], deep_research: dict[str, Any], visualization: dict[str, Any], query_generations: list[dict[str, Any]]) -> dict[str, Any]:
        candidate_by_id = {item["research_cluster_id"]: item for item in analyses}
        candidate_by_id[top_candidate["research_cluster_id"]] = top_candidate
        gap_candidates = list(candidate_by_id.values())
        return {
            "topic": topic,
            "scope": scope.model_dump(),
            "queries": {"scholar": scholar_queries, "adoption": [query for query, _ in adoption_query_specs], "adoption_families": [{"query": query, "family": family} for query, family in adoption_query_specs], "counter": counter_query},
            "scores": top_candidate["scores"],
            "label": top_candidate["label"],
            "gap_types": top_candidate.get("gap_types", []),
            "rationale": top_candidate.get("rationale", ""),
            "connected_points": top_candidate.get("connected_points", []),
            "gap_points": top_candidate.get("gap_points", []),
            "potential_points": top_candidate.get("potential_points", []),
            "opportunity_suggestions": top_candidate.get("opportunity_suggestions", []),
            "confirmed_barriers": top_candidate.get("confirmed_barriers", []),
            "inferred_barriers": top_candidate.get("inferred_barriers", []),
            "evidence": scholar.get("results", []) + _flatten_results(adoption),
            "counter_evidence": counter_evidence,
            "deep_research": deep_research,
            "visualization": visualization,
            "academic_evidence": [record.model_dump() for record in academic_records],
            "adoption_evidence": [record.model_dump() for record in adoption_records],
            "research_clusters": [cluster.model_dump() for cluster in research_clusters],
            "adoption_clusters": [cluster.model_dump() for cluster in adoption_clusters],
            "links": [link.model_dump() for link in links],
            "gap_candidates": gap_candidates,
            "scholar": scholar,
            "adoption": adoption,
            "scholar_query_generation": query_generations,
            "vocabulary": {**vocabulary.model_dump(), "industry_terms": vocabulary.terms},
            "gap_candidate": top_candidate,
        }

    def _unconfirmed_result(self, topic: str, scope: Any) -> dict[str, Any]:
        emit_event("note", {"text": "입력 분야를 확인하지 못해 추가 검색과 갭 생성을 중단합니다."}, stage="finalization", source="system")
        return {
            "topic": topic,
            "scope": scope.model_dump(),
            "queries": {"scholar": [], "adoption": [], "counter": ""},
            "scores": {"evidence_maturity": 0, "adoption_evidence": 0, "coverage_confidence": 0, "gap_priority": 0},
            "label": "unconfirmed_field",
            "gap_types": [],
            "rationale": "입력한 분야를 학술·산업 자료에서 확인하지 못했습니다.",
            "connected_points": [],
            "gap_points": [],
            "potential_points": [],
            "confirmed_barriers": [],
            "inferred_barriers": [],
            "evidence": [],
            "counter_evidence": [],
            "deep_research": {"used": False, "timed_out": False, "status": "skipped", "reason": "분야 미확인"},
            "visualization": {"requested": False, "artifact_received": False},
            "academic_evidence": [],
            "adoption_evidence": [],
            "research_clusters": [],
            "adoption_clusters": [],
            "links": [],
            "gap_candidates": [],
            "scholar": {"results": []},
            "adoption": [],
            "vocabulary": {"terms": [], "industry_terms": [], "query_families": {}, "mapping_confidence": 0, "rationale": ""},
            "gap_candidate": None,
        }


async def run_pipeline(topic: str, **kwargs: Any) -> dict[str, Any]:
    return await ResearchPipeline().run(topic, **kwargs)


def build_deadline_result(topic: str, reason: str) -> dict[str, Any]:
    """Return a renderable result when the request expires before first scoring."""
    return {
        "topic": topic,
        "scope": {"status": "unconfirmed", "selected_topics": [], "rationale": reason},
        "queries": {"scholar": [], "adoption": [], "counter": ""},
        "scores": {"evidence_maturity": 0, "adoption_evidence": 0, "coverage_confidence": 0, "gap_priority": 0},
        "label": "insufficient_evidence",
        "gap_types": ["possible_no_adoption_link"],
        "rationale": reason,
        "connected_points": [],
        "gap_points": [],
        "potential_points": [],
        "confirmed_barriers": [],
        "inferred_barriers": [],
        "evidence": [],
        "counter_evidence": [],
        "deep_research": {"used": False, "timed_out": True, "status": "not_started", "reason": reason},
        "visualization": {"requested": False, "artifact_received": False},
        "academic_evidence": [],
        "adoption_evidence": [],
        "research_clusters": [],
        "adoption_clusters": [],
        "links": [],
        "gap_candidates": [],
        "scholar": {"results": []},
        "adoption": [],
        "scholar_query_generation": [],
        "vocabulary": {"terms": [], "industry_terms": [], "query_families": {}, "mapping_confidence": 0, "rationale": ""},
        "gap_candidate": None,
        "analysis_status": "partial",
        "partial_reason": reason,
    }


def _tag_item(item: dict[str, Any], family: str) -> dict[str, Any]:
    tagged = dict(item)
    tagged["query_family"] = tagged.get("query_family") or family
    return tagged


def _tag_items(items: list[dict[str, Any]], family: str) -> list[dict[str, Any]]:
    return [_tag_item(item, family) for item in items]


def _merge_search_results(responses: list[dict[str, Any]]) -> dict[str, Any]:
    results = []
    seen: set[str] = set()
    for response in responses:
        for item in response.get("results", []):
            key = item.get("url") or item.get("title") or repr(item)
            if key in seen:
                continue
            seen.add(key)
            results.append(item)
    return {"totalCount": sum(response.get("totalCount", len(response.get("results", []))) for response in responses), "results": results, "searches": responses}


def _flatten_results(responses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for response in responses for item in response.get("results", [])]


def _clean_terms(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    terms: list[str] = []
    seen: set[str] = set()
    for value in values:
        term = str(value or "").strip()
        key = normalize_text(term)
        if not term or not key or key in seen:
            continue
        seen.add(key)
        terms.append(term)
    return terms


def _contains_hangul(value: str) -> bool:
    return any("\uac00" <= char <= "\ud7a3" for char in value)


def _fallback_academic_records(items: list[dict[str, Any]], existing_source_ids: set[str]) -> list[AcademicEvidenceRecord]:
    records: list[AcademicEvidenceRecord] = []
    for item in items:
        sid = source_id(item)
        if sid in existing_source_ids:
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        evidence_span = _fallback_evidence_span(item)
        technology = _fallback_research_technology(title)
        records.append(
            AcademicEvidenceRecord(
                record_id=stable_id("acad_fallback", sid, evidence_span),
                source_id=sid,
                source_url=item.get("url") or item.get("link") or "",
                source_title=title,
                published_at=item.get("published_at") or item.get("publishedAt"),
                citation_count=item.get("citationCount"),
                technology_raw=technology,
                technology_canonical=normalize_text(technology) or None,
                use_case_raw=_fallback_use_case(title),
                use_case_canonical=normalize_text(_fallback_use_case(title)) or None,
                context_raw=_source_hostname(item),
                context_canonical=normalize_text(_source_hostname(item)) or None,
                expected_value_raw=_fallback_expected_value(title),
                expected_value_canonical=normalize_text(_fallback_expected_value(title)) or None,
                canonical_claim=title,
                evidence_span=evidence_span,
                extraction_confidence=0.58,
                query_family=item.get("query_family"),
                is_replication=False,
                is_synthesis=_mentions_synthesis(title),
                is_real_world=_mentions_deployment_context(title),
                is_counter_evidence=False,
                result_direction="supports" if _mentions_deployment_context(title) else "unclear",
                institutions=[],
            )
        )
        existing_source_ids.add(sid)
    return records


def _topic_evidence_floor(academic_records: list[Any], scholar_items: list[dict[str, Any]]) -> int:
    structured_source_count = len({record.source_id for record in academic_records if getattr(record, "source_id", None)})
    search_source_count = len({item.get("url") or item.get("title") for item in scholar_items if item.get("url") or item.get("title")})
    source_count = max(structured_source_count, search_source_count)
    if source_count == 0:
        return 0

    breadth = min(source_count, 5) / 5 * 50
    citation_total = sum(min(_citation_count(item), 100) for item in scholar_items)
    citation_score = min(citation_total / 60 * 20, 20)

    deployment_hits = 0
    for record in academic_records:
        text = " ".join(
            str(value or "")
            for value in (
                getattr(record, "source_title", None),
                getattr(record, "canonical_claim", None),
                getattr(record, "evidence_span", None),
            )
        )
        if _mentions_deployment_context(text):
            deployment_hits += 1
    if deployment_hits == 0:
        deployment_hits = sum(1 for item in scholar_items if _mentions_deployment_context(f"{item.get('title', '')} {item.get('snippet', '')}"))
    deployment_score = min(deployment_hits, 2) / 2 * 15

    supports_count = sum(getattr(record, "result_direction", None) in {"supports", "mixed"} for record in academic_records)
    direction_score = 10 if supports_count >= 2 else 5 if supports_count == 1 else 0

    return max(0, min(round(breadth + citation_score + deployment_score + direction_score), 90))


def _topic_adoption_floor(adoption_records: list[Any]) -> int:
    uses = [record for record in adoption_records if getattr(record, "relation", None) == "uses"]
    if not uses:
        return 0

    by_subject: dict[str, list[int]] = {}
    for record in uses:
        subject = normalize_text(getattr(record, "subject_canonical", None) or getattr(record, "subject_raw", None))
        if not subject:
            continue
        by_subject.setdefault(subject, []).append(_adoption_record_points(record))

    organization_scores = []
    for scores in by_subject.values():
        ordered = sorted(scores, reverse=True)
        weighted = sum(score * (1 if index == 0 else 0.5 if index == 1 else 0) for index, score in enumerate(ordered))
        organization_scores.append(min(round(weighted), 35))

    if not organization_scores:
        return 0

    org_count = len(organization_scores)
    breadth_bonus = {1: 0, 2: 8, 3: 15}.get(org_count, 20)
    total = max(0, min(sum(sorted(organization_scores, reverse=True)[:3]) + breadth_bonus, 90))
    if all(getattr(record, "usage_context", None) == "vendor_product_integration" for record in uses):
        total = min(total, 70)
    return total


def _fallback_adoption_records(items: list[dict[str, Any]], existing_source_ids: set[str]) -> list[AdoptionEvidenceRecord]:
    records: list[AdoptionEvidenceRecord] = []
    for item in items:
        sid = source_id(item)
        if sid in existing_source_ids:
            continue
        text = f"{item.get('title', '')} {item.get('snippet', '')}"
        if not _looks_like_industry_adoption_result(item):
            continue
        subject = _trusted_industry_subject(item)
        if not subject:
            continue
        technology = _fallback_technology(item)
        if not technology:
            continue
        evidence_span = _fallback_evidence_span(item)
        if not evidence_span:
            continue
        stage = "production" if _mentions_production_use(text) else "unknown"
        use_case = _fallback_use_case(text)
        records.append(
            AdoptionEvidenceRecord(
                record_id=stable_id("adopt_fallback", sid, evidence_span),
                source_id=sid,
                source_url=item.get("url") or item.get("link") or "",
                source_title=item.get("title") or "제목 없음",
                published_at=item.get("published_at") or item.get("publishedAt"),
                technology_raw=technology,
                technology_canonical=normalize_text(technology),
                use_case_raw=use_case,
                use_case_canonical=normalize_text(use_case) or None,
                context_raw=_source_hostname(item),
                context_canonical=normalize_text(_source_hostname(item)) or None,
                expected_value_raw=_fallback_expected_value(text),
                expected_value_canonical=normalize_text(_fallback_expected_value(text)) or None,
                canonical_claim=evidence_span,
                evidence_span=evidence_span,
                extraction_confidence=0.6,
                query_family=item.get("query_family"),
                subject_raw=subject,
                subject_canonical=normalize_text(subject),
                relation="uses",
                usage_context="vendor_product_integration",
                adoption_stage=stage,
                deployment_unit=use_case,
                project_name=None,
                event_date=None,
                explicit_barriers=[],
            )
        )
        existing_source_ids.add(sid)
    return records


def _adoption_record_points(record: Any) -> int:
    stage = getattr(record, "adoption_stage", None) or "unknown"
    context = getattr(record, "usage_context", None) or "unknown"
    if context == "end_user_use":
        points = {"production": 45, "limited_deployment": 25, "pilot": 12, "unknown": 18}.get(stage, 18)
    elif context == "vendor_internal_use":
        points = {"production": 28, "limited_deployment": 16, "pilot": 8, "unknown": 12}.get(stage, 12)
    elif context == "vendor_product_integration":
        points = {"production": 35, "limited_deployment": 22, "pilot": 12, "unknown": 30}.get(stage, 30)
    else:
        points = 8

    text = " ".join(
        str(value or "")
        for value in (
            getattr(record, "canonical_claim", None),
            getattr(record, "evidence_span", None),
            getattr(record, "source_title", None),
        )
    )
    if _mentions_production_use(text):
        points = max(points, 28 if context == "vendor_product_integration" else 24)
    return points


def _citation_count(item: dict[str, Any]) -> int:
    for key in ("citationCount", "citation_count", "citedByCount", "cited_by_count", "citations"):
        value = item.get(key)
        if isinstance(value, (int, float)):
            return max(0, int(value))
        if isinstance(value, str):
            digits = re.sub(r"\D+", "", value)
            if digits:
                return int(digits)
    return 0


def _mentions_deployment_context(text: str) -> bool:
    normalized = normalize_text(text)
    markers = (
        "deployment",
        "deploy",
        "production",
        "serving",
        "inference",
        "edge",
        "real world",
        "real-world",
        "practical",
        "on-device",
        "온디바이스",
        "배포",
        "운영",
        "실서비스",
        "현장",
    )
    return any(marker in normalized for marker in markers)


def _mentions_production_use(text: str) -> bool:
    normalized = normalize_text(text)
    markers = (
        "production deployment",
        "production ready",
        "production-ready",
        "production serving",
        "deployed",
        "rollout",
        "customer deployment",
        "live system",
        "serving",
        "운영",
        "상용",
        "도입",
        "구축",
        "현장 적용",
    )
    return any(marker in normalized for marker in markers)


def _mentions_explicit_adoption_use(text: str) -> bool:
    normalized = normalize_text(text)
    required_markers = (
        "production",
        "production-ready",
        "deployed",
        "deployment",
        "inference",
        "serving",
        "performance",
        "optimization",
        "engineering",
        "rollout",
        "customer",
        "live system",
        "운영",
        "상용",
        "도입",
        "구축",
        "현장 적용",
        "서비스",
        "운용",
        "추론",
        "성능",
        "최적화",
        "엔지니어링",
        "애플리케이션",
        "어플리케이션",
        "솔루션",
        "플랫폼",
        "application",
        "solution",
        "platform",
    )
    weak_only_markers = (
        "survey",
        "overview",
        "comprehensive study",
        "tutorial",
        "day ",
        "patent",
        "future",
        "trend",
        "trends",
        "philosophy",
        "theory",
        "religion",
        "chapter",
        "book",
        "lecture",
        "course",
        "encyclopedia",
        "publisher",
        "wiki",
        "what is",
        "what are",
        "explained",
        "guide",
        "introduction",
        "how to",
        "how_to",
        "특허",
        "출원",
        "미래",
        "트렌드",
        "전망",
        "철학",
        "이론",
        "종교",
        "챕터",
        "장 ",
        "책",
        "강의",
        "강좌",
        "백과",
        "출판",
        "위키",
        "칼럼",
        "가이드",
        "소개",
        "입문",
        "무엇인가",
        "하는 방법",
        "는 방법",
        "활용 방법",
    )
    if any(marker in normalized for marker in required_markers):
        return not any(marker in normalized for marker in weak_only_markers)
    return False


def _looks_like_industry_adoption_result(item: dict[str, Any]) -> bool:
    host = _source_hostname(item)
    if not host or _is_non_industry_host(host):
        return False
    text = f"{item.get('title', '')} {item.get('snippet', '')}"
    if _is_weak_adoption_claim_text(text):
        return False
    if _mentions_explicit_adoption_use(text) and _has_query_overlap(item, text):
        return True
    return _looks_like_product_or_service_page(item, text)


def _trusted_industry_subject(item: dict[str, Any]) -> str | None:
    host = _source_hostname(item)
    if not host or _is_non_industry_host(host):
        return None
    title_subject = _subject_from_title(str(item.get("title") or ""))
    if title_subject and not _subject_overlaps_query(title_subject, item):
        return title_subject
    if not (
        host.endswith(".com")
        or host.endswith(".ai")
        or host.endswith(".io")
        or (host.endswith(".kr") and ".or.kr" not in host and ".ac.kr" not in host and ".go.kr" not in host)
    ):
        return None
    label = _registered_domain_label(host)
    if label in {"medium", "reddit", "github", "wikipedia", "arxiv"}:
        return None
    return _format_subject_label(label)


def _subject_from_title(title: str) -> str | None:
    title = title.strip()
    if not title:
        return None
    korean_match = re.match(r"^([A-Za-z0-9가-힣][A-Za-z0-9가-힣 .&+/-]{1,38}?)(?:가|이|은|는|에서)\s", title)
    if korean_match:
        candidate = korean_match.group(1).strip()
        if not _is_generic_adoption_subject(normalize_text(candidate)):
            return candidate
    english_match = re.match(
        r"^([A-Z][A-Za-z0-9 .&+/-]{1,38}?)(?:\s+(?:uses|deploys|launches|introduces|announces|builds|optimizes|serves|powers)\b|:)",
        title,
    )
    if english_match:
        candidate = english_match.group(1).strip()
        if not _is_generic_adoption_subject(normalize_text(candidate)):
            return candidate
    return None


def _subject_looks_like_title_fragment(subject: str, item: dict[str, Any]) -> bool:
    title = normalize_text(str(item.get("title") or ""))
    if not title or not subject:
        return False
    if not (title == subject or title.startswith(f"{subject} ")):
        return False

    host_label = normalize_text(_registered_domain_label(_source_hostname(item)))
    if subject == host_label:
        return False

    remainder = title[len(subject):].strip()
    explicit_subject_remainders = (
        "uses ",
        "deploys ",
        "launches ",
        "introduces ",
        "announces ",
        "builds ",
        "optimizes ",
        "serves ",
        "powers ",
        "가",
        "이",
        "은",
        "는",
        "에서",
    )
    return not any(remainder.startswith(marker) for marker in explicit_subject_remainders)


def _registered_domain_label(host: str) -> str:
    parts = [part for part in host.split(".") if part and part not in {"www", "m", "developer", "developers", "docs", "blog", "blogs", "cloud"}]
    if len(parts) >= 3 and parts[-2] in {"co", "or", "ac", "go"}:
        return parts[-3]
    if len(parts) >= 2:
        return parts[-2]
    return parts[0] if parts else host


def _format_subject_label(label: str) -> str:
    if 2 <= len(label) <= 4 and label.isascii():
        return label.upper()
    return label.replace("-", " ").title()


def _has_query_overlap(item: dict[str, Any], text: str) -> bool:
    query = str(item.get("query") or "")
    query_tokens = _meaningful_tokens(query)
    if not query_tokens:
        return True
    text_normalized = normalize_text(text)
    overlap_count = _query_overlap_count(query_tokens, text_normalized)
    required_count = 1 if len(query_tokens) == 1 else 2
    return overlap_count >= required_count


def _looks_like_product_or_service_page(item: dict[str, Any], text: str) -> bool:
    title = normalize_text(str(item.get("title") or ""))
    if not title:
        return False
    if not _has_product_or_service_hint(title):
        return False
    title_token_count = len(title.split())
    query_tokens = _meaningful_tokens(str(item.get("query") or ""))
    if not query_tokens:
        return False
    overlap_count = _query_overlap_count(query_tokens, normalize_text(text))
    return overlap_count >= 2 and title_token_count <= 10


def _has_product_or_service_hint(normalized_title: str) -> bool:
    hints = (
        "ai",
        "api",
        "sdk",
        "software",
        "service",
        "services",
        "solution",
        "solutions",
        "platform",
        "application",
        "app",
        "system",
        "tool",
        "tools",
        "제품",
        "서비스",
        "솔루션",
        "플랫폼",
        "애플리케이션",
        "어플리케이션",
        "시스템",
        "도구",
    )
    return any(hint in normalized_title for hint in hints)


def _query_overlap_count(query_tokens: list[str], normalized_text: str) -> int:
    return sum(1 for token in query_tokens if token in normalized_text)


def _subject_overlaps_query(subject: str, item: dict[str, Any]) -> bool:
    subject_normalized = normalize_text(subject)
    if not subject_normalized:
        return False
    return any(token in subject_normalized for token in _meaningful_tokens(str(item.get("query") or "")))


def _meaningful_tokens(text: str) -> list[str]:
    stopwords = {
        "production",
        "deployment",
        "deploy",
        "case",
        "study",
        "inference",
        "serving",
        "cloud",
        "customer",
        "real",
        "world",
        "산업",
        "적용",
        "사례",
        "상용",
        "서비스",
        "현장",
    }
    tokens = []
    for token in normalize_text(text).split():
        if token in stopwords:
            continue
        if len(token) < 3 and not _contains_hangul(token):
            continue
        tokens.append(token)
    return tokens[:8]


def _is_non_industry_host(host: str) -> bool:
    host_label = _registered_domain_label(host)
    media_labels = {
        "news",
        "daily",
        "times",
        "press",
        "media",
        "magazine",
        "herald",
        "tribune",
    }
    if host_label in media_labels or host_label.endswith(tuple(media_labels)):
        return True
    academic_venue_labels = {
        "neurips",
        "icml",
        "iclr",
        "cvpr",
        "thecvf",
        "aclweb",
        "aclanthology",
        "aaai",
        "ijcai",
        "siggraph",
    }
    if host_label in academic_venue_labels:
        return True
    generic_blocked_labels = {
        "news",
        "journal",
        "research",
        "science",
        "conference",
        "summit",
        "proceedings",
        "paper",
        "preprint",
        "publication",
        "scholar",
        "openreview",
        "openaccess",
        "wiki",
        "blog",
    }
    if any(marker in host or marker in host_label for marker in generic_blocked_labels):
        return True
    blocked = (
        "arxiv.org",
        "doi.org",
        "springer.com",
        "link.springer.com",
        "jstor.org",
        "wikipedia.org",
        "reddit.com",
        "linkedin.com",
        "medium.com",
        "naver.com",
        "blog.naver.com",
        "tistory.com",
        "velog.io",
        "brunch.co.kr",
        "towardsdatascience.com",
        "researchgate.net",
        "mdpi.com",
        "acm.org",
        "ieee.org",
        "sciencedirect.com",
        "embeddedvisionsummit.com",
        "koreascience.kr",
        "kci.go.kr",
        "scienceon.kisti.re.kr",
        "etnews.com",
        "dbpia.co.kr",
        "dcs.or.kr",
        "youtube.com",
        "github.com",
    )
    return any(host == domain or host.endswith(f".{domain}") for domain in blocked)


def _source_hostname(item: dict[str, Any]) -> str:
    url = item.get("url") or item.get("link") or ""
    try:
        return urlsplit(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def _fallback_technology(item: dict[str, Any]) -> str | None:
    query = str(item.get("query") or "")
    title = str(item.get("title") or "")
    for marker in (
        " production",
        " inference",
        " cloud",
        " case study",
        " deployment",
        " 산업",
        " 상용",
        " 현장",
    ):
        query = query.replace(marker, " ")
    candidate = query.strip() or title.strip()
    return candidate[:120] if candidate else None


def _fallback_research_technology(title: str) -> str:
    title = title.strip()
    if ":" in title:
        head = title.split(":", 1)[0].strip()
        if 4 <= len(head) <= 120:
            return head
    return title[:120]


def _fallback_use_case(text: str) -> str:
    normalized = normalize_text(text)
    if "serving" in normalized:
        return "serving"
    if "inference" in normalized:
        return "inference optimization"
    if "cloud" in normalized:
        return "cloud deployment"
    if "edge" in normalized:
        return "edge deployment"
    if "운영" in normalized or "서비스" in normalized:
        return "서비스 운영"
    if "현장" in normalized:
        return "현장 적용"
    return "production deployment"


def _mentions_synthesis(text: str) -> bool:
    normalized = normalize_text(text)
    markers = (
        "survey",
        "review",
        "benchmark",
        "comprehensive",
        "동향",
        "분석",
        "벤치마크",
        "비교",
    )
    return any(marker in normalized for marker in markers)


def _fallback_expected_value(text: str) -> str | None:
    normalized = normalize_text(text)
    if "cost" in normalized or "비용" in normalized:
        return "cost efficiency"
    if "latency" in normalized or "latency" in normalized:
        return "latency reduction"
    if "memory" in normalized or "메모리" in normalized:
        return "memory reduction"
    if "efficient" in normalized or "효율" in normalized:
        return "efficiency"
    return None


def _fallback_evidence_span(item: dict[str, Any]) -> str:
    title = str(item.get("title") or "").strip()
    snippet = str(item.get("snippet") or "").strip()
    if title and snippet:
        return f"{title}: {snippet[:240]}"
    return title or snippet[:240]


def _has_actionable_adoption_locus(extraction: Any) -> bool:
    subject = normalize_text(extraction.subject_canonical or extraction.subject_raw)
    if not subject or _is_generic_adoption_subject(subject):
        return False
    if _is_weak_adoption_claim_text(getattr(extraction, "evidence_span", None)):
        return False
    if _subject_conflicts_with_technical_fields(subject, extraction):
        return False

    if extraction.relation != "uses":
        return extraction.relation == "does_not_use"

    has_specific_locus = any(
        normalize_text(value)
        for value in (
            extraction.deployment_unit,
            extraction.project_name,
            extraction.use_case_raw,
            extraction.context_raw,
        )
    )
    if not has_specific_locus:
        return False

    if not extraction.usage_context:
        return False

    return True


def _is_weak_adoption_claim_text(text: str | None) -> bool:
    normalized = normalize_text(text)
    weak_markers = (
        "patent",
        "survey",
        "overview",
        "review",
        "tutorial",
        "future",
        "trend",
        "trends",
        "philosophy",
        "theory",
        "religion",
        "chapter",
        "book",
        "lecture",
        "course",
        "encyclopedia",
        "publisher",
        "wiki",
        "what is",
        "what are",
        "explained",
        "guide",
        "introduction",
        "how to",
        "how_to",
        "특허",
        "출원",
        "동향",
        "리뷰",
        "개요",
        "튜토리얼",
        "미래",
        "트렌드",
        "전망",
        "철학",
        "이론",
        "종교",
        "챕터",
        "책",
        "강의",
        "강좌",
        "백과",
        "출판",
        "위키",
        "칼럼",
        "가이드",
        "소개",
        "입문",
        "무엇인가",
        "하는 방법",
        "는 방법",
        "활용 방법",
    )
    return any(marker in normalized for marker in weak_markers)


def _subject_conflicts_with_technical_fields(subject: str, extraction: Any) -> bool:
    technical_text = " ".join(
        normalize_text(value)
        for value in (
            getattr(extraction, "technology_raw", None),
            getattr(extraction, "technology_canonical", None),
            getattr(extraction, "use_case_raw", None),
            getattr(extraction, "use_case_canonical", None),
            getattr(extraction, "context_raw", None),
            getattr(extraction, "context_canonical", None),
            getattr(extraction, "expected_value_raw", None),
            getattr(extraction, "expected_value_canonical", None),
        )
        if value
    )
    if technical_text and _is_subphrase(subject, technical_text):
        return True

    evidence = normalize_text(getattr(extraction, "evidence_span", None))
    if evidence and evidence.startswith(subject):
        remainder = evidence[len(subject):].strip()
        first_words = tuple(remainder.split()[:2])
        technical_heads = {
            "classification",
            "segmentation",
            "analysis",
            "optimization",
            "technique",
            "techniques",
            "method",
            "methods",
            "model",
            "models",
            "framework",
            "system",
            "survey",
            "review",
            "분류",
            "분석",
            "최적화",
            "기법",
            "방법",
            "모델",
            "시스템",
            "동향",
        }
        if first_words and first_words[0] in technical_heads:
            return True
    return False


def _is_subphrase(needle: str, haystack: str) -> bool:
    if not needle or not haystack:
        return False
    return needle == haystack or f" {needle} " in f" {haystack} " or haystack.startswith(f"{needle} ")


def _is_generic_adoption_subject(subject: str) -> bool:
    generic_subjects = {
        "ai",
        "artificial intelligence",
        "public services",
        "ai public services",
        "ai work processes",
        "work processes",
        "organizations",
        "organization",
        "society",
        "modern society",
        "research",
        "researchers",
        "qualitative research",
        "education",
        "students",
        "technology",
        "social structure",
        "model",
        "models",
        "공공 서비스",
        "ai 공공 서비스",
        "업무 프로세스",
        "조직",
        "사회",
        "현대 사회",
        "연구",
        "연구자",
        "교육",
        "학생",
        "기술",
        "사회 구조",
        "모델",
    }
    if subject in generic_subjects:
        return True
    generic_markers = (
        " theory",
        " philosophy",
        " research",
        " study",
        " studies",
        " materialism",
        " ideology",
        " political economy",
        " 이론",
        " 철학",
        " 연구",
        " 방법론",
        " 유물론",
        " 이데올로기",
    )
    if any(subject.endswith(marker) for marker in generic_markers):
        return True

    academic_concept_markers = (
        "dialectic",
        "critical theory",
        "ontology",
        "epistemology",
        "변증법",
        "비판 이론",
        "존재론",
        "인식론",
    )
    return any(marker in subject for marker in academic_concept_markers)


def _extract_counter_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for event in result.get("events", []):
        data = event.get("data") or {}
        if event.get("type") == "data-search-references":
            items.extend(_tag_items(data.get("references", []), "context"))
        elif event.get("type") == "data-search-chunks":
            chunks = data.get("referenceChunks", data.get("reference_chunks", []))
            items.extend(_tag_items(chunks, "context"))
    deduped = []
    seen = set()
    for item in items:
        key = item.get("url") or item.get("title") or repr(item)
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def _extract_counter_evidence(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"kind": "reference", **item} for item in _extract_counter_items(result)]


def _extract_report(result: dict[str, Any]) -> str:
    return "".join(event.get("delta", "") for event in result.get("events", []) if event.get("type") == "text-delta")


def _has_event_type(result: dict[str, Any], event_type: str) -> bool:
    return any(event.get("type") == event_type for event in result.get("events", []))


def _agent_search_failed(result: dict[str, Any]) -> bool:
    return bool(result.get("timed_out") or result.get("stream_error") or _has_event_type(result, "data-error"))


def _cluster_summary(cluster: Any) -> dict[str, Any]:
    return {
        "cluster_id": cluster.cluster_id,
        "technology": cluster.technology,
        "use_case": cluster.use_case,
        "context": cluster.context,
        "expected_value": cluster.expected_value,
        "subject": getattr(cluster, "subject", None),
        "stage": getattr(cluster, "max_stage_attained", None),
        "relation": getattr(cluster, "latest_relation", None),
    }


def _query_family_count(query_families: dict[str, list[str]], query_specs: list[tuple[str, str]]) -> int:
    if hasattr(query_families, "model_dump"):
        query_families = query_families.model_dump()
    if query_families:
        return sum(bool(query_families.get(family)) for family in ("technology", "use_case", "context"))
    return len({family for _, family in query_specs if family in {"technology", "use_case", "context"}})


def _maturity_signals(cluster: ResearchCluster) -> dict[str, float]:
    return {
        "unique_papers": cluster.unique_paper_count,
        "replications": cluster.replication_count,
        "syntheses": cluster.synthesis_count,
        "real_world": cluster.real_world_count,
        "supports": cluster.supports_count,
        "mixed": cluster.mixed_count,
        "contradicts": cluster.contradicts_count,
        "unclear": cluster.unclear_count,
    }


def _top_analysis(analyses: list[dict[str, Any]]) -> dict[str, Any]:
    if not analyses:
        return {
            "research_cluster_id": "",
            "scores": {"evidence_maturity": 0, "adoption_evidence": 0, "coverage_confidence": 0, "gap_priority": 0},
            "label": "insufficient_evidence",
            "gap_types": [],
            "links": [],
            "candidate_connections": [],
            "confirmed_barriers": [],
            "inferred_barriers": [],
            "should_deep_research": False,
            "deep_research_reasons": [],
            "research_cluster": {},
        }
    no_gap = [item for item in analyses if item.get("label") == "no_gap"]
    if no_gap:
        return max(no_gap, key=lambda item: item["scores"].get("adoption_evidence", 0))
    emerging = [item for item in analyses if item.get("label") == "emerging_adoption"]
    if emerging:
        return max(emerging, key=lambda item: item["scores"].get("adoption_evidence", 0))
    return max(analyses, key=lambda item: item["scores"].get("gap_priority", 0))


def _can_suggest_opportunities(analysis: dict[str, Any], narrative: dict[str, Any]) -> bool:
    if analysis.get("label") not in {"gap_candidate", "emerging_adoption"}:
        return False
    return bool(narrative.get("gap_points"))


def _clean_opportunity_suggestions(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned = []
    for value in values:
        text = " ".join(str(value or "").split())
        if not text or text in cleaned:
            continue
        cleaned.append(text)
        if len(cleaned) >= 3:
            break
    return cleaned


def _short_phrase(value: Any, fallback: str) -> str:
    text = " ".join(str(value or "").split())
    return text or fallback


def _compact_narrative_link(link: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "link_type",
        "technology_match",
        "use_case_match",
        "context_match",
        "expected_value_match",
        "matched_on",
        "missing_on",
        "explanation",
        "confidence",
    )
    return {field: link[field] for field in fields if link.get(field) not in (None, "", [])}


def _narrative_agent_payload(
    analysis: dict[str, Any],
    deterministic: dict[str, Any],
    research_clusters: list[ResearchCluster],
    adoption_clusters: list[AdoptionCluster],
) -> dict[str, Any]:
    linked_adoption_ids = {
        link.get("adoption_cluster_id")
        for link in analysis.get("links", [])
        if isinstance(link, dict) and link.get("adoption_cluster_id")
    }
    return {
        "task": "Return concise Korean narrative fields. Keep label, scores, gap_types, and link_type unchanged.",
        "locked": {
            "label": analysis.get("label"),
            "scores": analysis.get("scores", {}),
            "gap_types": analysis.get("gap_types", []),
        },
        "computed_narrative": deterministic,
        "research_cluster": analysis.get("research_cluster", {}),
        "linked_research_clusters": [
            _cluster_summary(cluster)
            for cluster in research_clusters
            if cluster.cluster_id == analysis.get("research_cluster_id")
        ],
        "linked_adoption_clusters": [
            _cluster_summary(cluster)
            for cluster in adoption_clusters
            if cluster.cluster_id in linked_adoption_ids
        ][:5],
        "links": [_compact_narrative_link(link) for link in analysis.get("links", [])[:5] if isinstance(link, dict)],
        "candidate_connections": analysis.get("candidate_connections", [])[:5],
        "rules": [
            "Do not change scores, label, gap_types, or link_type.",
            "Only create opportunity_suggestions when computed_narrative.gap_points is non-empty.",
            "Keep opportunity_suggestions concrete: product/service form, target user, and validation action.",
        ],
    }


def _deterministic_opportunity_suggestions(analysis: dict[str, Any], narrative: dict[str, Any]) -> list[str]:
    if not _can_suggest_opportunities(analysis, narrative):
        return []
    cluster = analysis.get("research_cluster") or {}
    technology = _short_phrase(cluster.get("technology"), "해당 기술")
    use_case = _short_phrase(cluster.get("use_case"), "해당 사용 사례")
    context = _short_phrase(cluster.get("context"), "대상 산업 환경")
    expected_value = _short_phrase(cluster.get("expected_value"), "운영 성과")
    gap_types = set(analysis.get("gap_types") or [])

    suggestions = []

    def add(text: str) -> None:
        if text not in suggestions:
            suggestions.append(text)

    if gap_types & {"no_adoption_link", "possible_no_adoption_link"}:
        add(f"{technology}의 {use_case} 학술 근거를 {context} 담당 팀이 시험할 수 있는 검증 패키지로 제공하면 직접 도입 사례가 없는 갭을 확인할 수 있습니다.")
    if "stage_gap" in gap_types:
        add(f"파일럿·제한 운영 단계의 {technology} 적용 사례를 대상으로 {expected_value} 운영 지표를 수집하는 전환 리포트를 제공하면 정식 운영 근거 부족을 줄일 수 있습니다.")
    if "context_gap" in gap_types:
        add(f"{context} 환경에 맞춘 {technology} 적용 벤치마크를 {use_case} 담당 조직에 제공하면 연구 조건과 산업 맥락 차이를 검증할 수 있습니다.")
    if "technology_substitution" in gap_types:
        add(f"{use_case}를 이미 다른 기술로 처리하는 조직에 {technology} 비교 평가 도구를 제공하면 대체 기술 대비 적용 가능성을 검증할 수 있습니다.")
    if "barrier_gap" in gap_types:
        add(f"{technology} 도입 장벽을 기준으로 보안·비용·운영 체크리스트를 만든 뒤 {use_case} 담당 팀에 사전 진단 서비스로 제공할 수 있습니다.")
    if "outcome_gap" in gap_types:
        add(f"{technology} 적용 전후의 {expected_value}를 같은 기준으로 측정하는 성과 검증 대시보드를 제공하면 연구 효과와 운영 결과 차이를 확인할 수 있습니다.")
    if not suggestions:
        add(f"{technology}의 {use_case} 학술 근거와 산업 검색에서 비어 있는 조건을 묶어 {context} 대상 검증 과제로 제안할 수 있습니다.")
    return _clean_opportunity_suggestions(suggestions)


def _deterministic_narrative(analysis: dict[str, Any]) -> dict[str, Any]:
    scores = analysis.get("scores", {})
    label = analysis.get("label", "insufficient_evidence")
    cluster = analysis.get("research_cluster", {})
    technology = cluster.get("technology") or "해당 기술"
    use_case = cluster.get("use_case") or "해당 사용 사례"
    links = analysis.get("links", [])
    direct_count = sum(link.get("link_type") == "direct" for link in links)
    partial_count = sum(link.get("link_type") == "partial" for link in links)
    blocked_count = sum(link.get("link_type") == "blocked" for link in links)

    connected_points = []
    if direct_count:
        connected_points.append(f"{technology}의 {use_case} 적용과 산업 도입 사이에서 직접 연결 {direct_count}건을 확인했습니다.")
    if partial_count:
        connected_points.append(f"연구와 산업 사례 사이의 부분 연결 {partial_count}건을 확인했습니다.")

    gap_messages = {
        "no_adoption_link": "현재 검색 범위에서 연구 적용과 직접 연결되는 산업 도입을 확인하지 못했습니다.",
        "possible_no_adoption_link": "산업 도입 연결을 판단할 구조화된 근거가 부족합니다.",
        "stage_gap": "산업 사례가 파일럿 또는 제한 운영 단계에 머물러 정식 운영까지 이어진 근거가 부족합니다.",
        "context_gap": "연구와 산업 사례의 배포 환경 또는 사용 맥락이 일치하지 않습니다.",
        "technology_substitution": "산업에서는 같은 사용 사례를 다른 기술로 해결하는 정황이 확인됩니다.",
        "barrier_gap": "도입 중단·거절·금지와 관련된 장벽 근거가 확인됩니다.",
        "outcome_gap": "운영은 확인됐지만 연구에서 기대한 결과와 실제 결과가 일치하지 않습니다.",
    }
    gap_points = [gap_messages[gap_type] for gap_type in analysis.get("gap_types", []) if gap_type in gap_messages]
    if blocked_count:
        gap_points.append(f"도입 중단 또는 거절 연결 {blocked_count}건을 확인했습니다.")

    potential_points = []
    for candidate in analysis.get("candidate_connections", [])[:2]:
        missing = candidate.get("missing_dimensions") or candidate.get("required_validation") or []
        if missing:
            potential_points.append(f"추가 확인이 필요한 차원: {', '.join(missing)}.")
    potential_points = list(dict.fromkeys(potential_points))

    return {
        "rationale": (
            f"검색된 연구 근거 성숙도 {scores.get('evidence_maturity', 0)}/100, "
            f"산업 도입 증거 {scores.get('adoption_evidence', 0)}/100, "
            f"검색 커버리지 {scores.get('coverage_confidence', 0)}/100를 기준으로 "
            f"{label}을 판정했습니다. 직접 연결 {direct_count}건, 부분 연결 {partial_count}건, "
            f"중단·거절 연결 {blocked_count}건입니다."
        ),
        "connected_points": connected_points[:5],
        "gap_points": list(dict.fromkeys(gap_points))[:5],
        "potential_points": potential_points[:5],
    }
