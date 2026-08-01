"""Research-to-Reality pipeline with deterministic linkage and scoring."""

from __future__ import annotations

import asyncio
from typing import Any

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
        query_specs = self._build_adoption_query_specs(vocabulary, adoption_queries)
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
        analyses = self._evaluate_all(research_clusters, adoption_clusters, links, coverage, scope.status == "unconfirmed")
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

        counter_query = self._counter_query(topic, top_candidate)
        counter_result = await self._run_adversarial_verifier(counter_query)
        counter_items = _extract_counter_items(counter_result)
        counter_evidence = _extract_counter_evidence(counter_result)
        counter_records, counter_meta = await self._extract_adoption_records(counter_items)
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
                "performed": True,
                "timed_out": _agent_search_failed(counter_result),
                "result_count": len({item.get("url") or item.get("title") for item in counter_items if item.get("url") or item.get("title")}),
            },
        )
        analyses = self._evaluate_all(research_clusters, adoption_clusters, links, coverage, False)
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
            reason="반증 검색까지 반영한 잠정 판정입니다.",
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
                    "performed": True,
                    "timed_out": _agent_search_failed(counter_result),
                    "result_count": len({item.get("url") or item.get("title") for item in [*counter_items, *deep_items] if item.get("url") or item.get("title")}),
                },
            )
            analyses = self._evaluate_all(research_clusters, adoption_clusters, links, coverage, False)
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

    def _build_adoption_query_specs(self, vocabulary: Any, overrides: list[str] | None) -> list[tuple[str, str]]:
        if overrides:
            return [(query, "manual") for query in overrides[: self.runtime.max_adoption_queries]]
        families = vocabulary.query_families.model_dump()
        if not any(families.values()):
            families = {"technology": vocabulary.terms, "use_case": [], "context": []}

        technologies = _clean_terms(families.get("technology", [])) or _clean_terms(vocabulary.terms)
        use_cases = _clean_terms(families.get("use_case", []))
        contexts = _clean_terms(families.get("context", []))

        specs: list[tuple[str, str]] = []
        if technologies and use_cases:
            specs.append((f"{technologies[0]} {use_cases[0]} production deployment", "technology+use_case"))
        if technologies and len(use_cases) > 1:
            specs.append((f"{technologies[0]} {use_cases[1]} production deployment", "technology+use_case"))
        if technologies and contexts:
            specs.append((f"{technologies[0]} {contexts[0]} engineering blog production", "technology+context"))
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
            tagged["results"] = [_tag_item(item, family) for item in response.get("results", [])]
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
            emit_event("note", {"text": "학술 근거 구조화 시간 예산이 끝나 검색 결과만으로 잠정 판정합니다."}, stage="academic_extraction", source="system")
            return [], {"total_relevant_results": len(items), "structured_record_count": 0, "failed_count": len(items), "timed_out": True}
        except Exception as exc:
            emit_event(
                "note",
                {"text": "학술 근거 구조화 실패 → 검색 결과 원문만 남기고 잠정 판정합니다.", "error": str(exc)},
                stage="academic_extraction",
                source="system",
            )
            return [], {
                "total_relevant_results": len(items),
                "structured_record_count": 0,
                "failed_count": len(items),
                "error": str(exc),
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
        return records, {
            "total_relevant_results": len(items),
            "structured_record_count": len(records),
            "failed_count": max(0, len(items) - len(records)),
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
            emit_event("note", {"text": "산업 도입 근거 구조화 시간 예산이 끝나 확인된 기록만으로 잠정 판정합니다."}, stage="adoption_extraction", source="system")
            return [], {"total_relevant_results": len(items), "structured_record_count": 0, "failed_count": len(items), "timed_out": True}
        except Exception as exc:
            emit_event(
                "note",
                {"text": "산업 도입 근거 구조화 실패 → 검색 결과 원문과 반증 검색으로 잠정 판정합니다.", "error": str(exc)},
                stage="adoption_extraction",
                source="system",
            )
            return [], {
                "total_relevant_results": len(items),
                "structured_record_count": 0,
                "failed_count": len(items),
                "error": str(exc),
            }
        records: list[AdoptionEvidenceRecord] = []
        for extraction in batch.records:
            if not extraction.is_relevant or extraction.extraction_confidence < 0.55:
                continue
            item = source_items[extraction.source_index] if extraction.source_index < len(source_items) else None
            technology_canonical = normalize_text(extraction.technology_canonical or extraction.technology_raw) or None
            subject_canonical = normalize_text(extraction.subject_canonical or extraction.subject_raw) or None
            if not item or not extraction.evidence_span or not technology_canonical or not subject_canonical:
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
        return records, {
            "total_relevant_results": len(items),
            "structured_record_count": len(records),
            "failed_count": max(0, len(items) - len(records)),
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
        )

    def _evaluate_all(self, research_clusters: list[ResearchCluster], adoption_clusters: list[AdoptionCluster], links: list[ClusterLink], coverage: dict[str, Any], field_not_confirmed: bool) -> list[dict[str, Any]]:
        adoption_by_id = {cluster.cluster_id: cluster for cluster in adoption_clusters}
        by_research: dict[str, list[ClusterLink]] = {}
        for link in links:
            by_research.setdefault(link.research_cluster_id, []).append(link)
        analyses = []
        for research in research_clusters:
            cluster_links = by_research.get(research.cluster_id, [])
            adoption_breakdown = calculate_adoption_evidence(cluster_links, adoption_by_id)
            coverage_breakdown = coverage
            maturity = research.evidence_maturity or 0
            adoption_score = adoption_breakdown["total"]
            coverage_score = coverage_breakdown["total"]
            gap_types = classify_gap_types(research, cluster_links, adoption_by_id, coverage_score)
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
            analyses.append(
                {
                    "research_cluster_id": research.cluster_id,
                    "research_cluster": research.model_dump(),
                    "scores": {"evidence_maturity": maturity, "adoption_evidence": adoption_score, "coverage_confidence": coverage_score, "gap_priority": priority},
                    "score_breakdown": {
                        "evidence_maturity": {"total": maturity, "signals": _maturity_signals(research), "details": {}},
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
        # 점수와 label은 이미 결정론적으로 계산됐다. 외부 모델을 최종 응답의
        # critical path에 두면 모델 지연 때문에 결과 자체가 늦어지므로, 설명도
        # 같은 계산 결과에서 즉시 조합한다.
        emit_event(
            "note",
            {"text": "최종 설명은 계산된 점수·연결·갭 유형을 바탕으로 정리합니다."},
            stage="finalization",
            source="system",
        )
        return _deterministic_narrative(analysis)

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
    return max(analyses, key=lambda item: item["scores"].get("gap_priority", 0))


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
            f"연구 근거 성숙도 {scores.get('evidence_maturity', 0)}/100, "
            f"산업 도입 증거 {scores.get('adoption_evidence', 0)}/100, "
            f"검색 커버리지 {scores.get('coverage_confidence', 0)}/100를 기준으로 "
            f"{label}을 판정했습니다. 직접 연결 {direct_count}건, 부분 연결 {partial_count}건, "
            f"중단·거절 연결 {blocked_count}건입니다."
        ),
        "connected_points": connected_points[:5],
        "gap_points": list(dict.fromkeys(gap_points))[:5],
        "potential_points": potential_points[:5],
    }
