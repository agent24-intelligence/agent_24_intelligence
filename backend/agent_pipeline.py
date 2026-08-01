"""Full application-gap research pipeline orchestration."""

import os
from typing import Any

from events import emit_event
from liner_client import LinerClient
from openai_agents import ResearchAgents


class ResearchPipeline:
    """Run the staged research and verification workflow."""

    def __init__(self, liner: LinerClient | None = None, agents: ResearchAgents | None = None):
        self.liner = liner or LinerClient()
        self.agents = agents or ResearchAgents()

    async def run(
        self,
        topic: str,
        *,
        scholar_query: str | None = None,
        adoption_queries: list[str] | None = None,
        max_results: int = 10,
    ) -> dict[str, Any]:
        scope = await self._calibrate_scope(topic)
        scholar_queries, query_generations = await self._build_scholar_queries(
            topic,
            scope,
            scholar_query,
        )

        scholar = await self._run_scholar_scout(scholar_queries, max_results=max_results)
        vocabulary = await self._run_vocabulary_bridge(topic, scholar)

        adoption_queries = adoption_queries or vocabulary.terms
        adoption = await self._run_adoption_scout(adoption_queries, max_results=max_results)

        gap_candidate = await self._run_gap_candidate(topic, scholar, adoption)
        counter_query = self._counter_query(topic, gap_candidate.rationale)
        counter_result = await self._run_adversarial_verifier(counter_query)
        counter_evidence = _extract_counter_evidence(counter_result)
        final_gap = _downgrade_for_counter_evidence(gap_candidate.model_dump(), counter_evidence)

        deep_research = await self._run_conditional_deep_research(
            topic,
            final_gap,
            counter_evidence,
        )
        visualization = await self._run_gap_map(
            topic,
            final_gap,
            max_results=max_results,
        )

        result = {
            "topic": topic,
            "scope": scope.model_dump(),
            "queries": {
                "scholar": scholar_queries,
                "adoption": adoption_queries,
                "counter": counter_query,
            },
            "scores": {
                "evidence_maturity": final_gap["evidence_maturity"],
                "adoption_evidence": final_gap["adoption_evidence"],
                "coverage_confidence": final_gap["coverage_confidence"],
            },
            "label": final_gap["gap_label"],
            "rationale": final_gap["rationale"],
            # 어떤 게 연결됐고, 어떤 게 안 됐고(진짜 갭), 뭐가 더 연결될 여지가 있는지
            # 사용자가 바로 볼 수 있게 rationale 문단과 별개로 최상위에도 노출한다.
            "connected_points": final_gap.get("connected_points", []),
            "gap_points": final_gap.get("gap_points", []),
            "potential_points": final_gap.get("potential_points", []),
            "opportunity_suggestions": final_gap.get("opportunity_suggestions", []),
            "evidence": scholar.get("results", []) + _flatten_results(adoption),
            "counter_evidence": counter_evidence,
            "deep_research": deep_research,
            "visualization": visualization,
            # Keep the raw Search responses available during the transition period.
            "scholar": scholar,
            "adoption": adoption,
            "scholar_query_generation": query_generations,
            "vocabulary": vocabulary.model_dump(),
            "gap_candidate": final_gap,
        }
        emit_event(
            "finish",
            {
                "topic": topic,
                "label": result["label"],
                "scholar_results": len(scholar.get("results", [])),
                "adoption_searches": len(adoption),
                "counter_evidence": len(counter_evidence),
            },
            stage="gap_map",
            source="system",
        )
        return result

    async def _calibrate_scope(self, topic: str):
        emit_event(
            "note",
            {"text": "Scope Calibrator: 입력 주제의 범위를 판정합니다."},
            stage="scope_calibrator",
            source="system",
        )
        return await self.agents.scope(topic)

    async def _build_scholar_queries(
        self,
        topic: str,
        scope: Any,
        scholar_query: str | None,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        if scholar_query:
            return [scholar_query], [
                {"query": scholar_query, "rationale": "디버깅용 scholar_query를 그대로 사용합니다."}
            ]

        emit_event(
            "note",
            {"text": "Scope 결과를 학술 표준 용어와 배포 맥락이 포함된 Scholar 쿼리로 정제합니다."},
            stage="scholar_scout",
            source="system",
        )
        generated = []
        for selected_topic in scope.selected_topics:
            query_result = await self.agents.scholar_query(selected_topic, scope)
            generated.append(query_result.model_dump())
        return [item["query"] for item in generated], generated

    async def _run_scholar_scout(self, queries: list[str], *, max_results: int) -> dict[str, Any]:
        emit_event(
            "note",
            {"text": f"Scholar Scout: 정제된 학술 쿼리 {len(queries)}개로 검색합니다."},
            stage="scholar_scout",
            source="system",
        )
        responses = []
        for query in queries:
            responses.append(
                await self.liner.search_scholar(
                    query,
                    lang="ko",
                    max_results=max_results,
                    stage="scholar_scout",
                )
            )
        return _merge_search_results(responses)

    async def _run_vocabulary_bridge(self, topic: str, scholar: dict[str, Any]):
        emit_event(
            "note",
            {"text": "Vocabulary Bridge: 학술 용어를 산업 검색어로 변환합니다."},
            stage="vocabulary_bridge",
            source="system",
        )
        return await self.agents.vocabulary_bridge(topic, scholar)

    async def _run_adoption_scout(self, queries: list[str], *, max_results: int) -> list[dict[str, Any]]:
        emit_event(
            "note",
            {"text": f"Adoption Scout: 산업 검색어 {len(queries)}개로 도입 증거를 확인합니다."},
            stage="adoption_scout",
            source="system",
        )
        adoption = []
        for query in queries:
            adoption.append(
                await self.liner.search_web(
                    query,
                    lang="ko",
                    max_results=max_results,
                    stage="adoption_scout",
                )
            )
        return adoption

    async def _run_gap_candidate(self, topic: str, scholar: dict[str, Any], adoption: list[dict[str, Any]]):
        emit_event(
            "note",
            {"text": "Gap Candidate Generator: 학술·산업 근거를 대조합니다."},
            stage="gap_candidate_generator",
            source="system",
        )
        return await self.agents.gap_candidate(topic, scholar, adoption)

    def _counter_query(self, topic: str, rationale: str) -> str:
        return (
            f"Find strong counter-evidence that {topic} is already widely deployed in industry. "
            f"Check product documentation, customer case studies, open-source deployments, standards, "
            f"job postings, procurement, and industry reports. Candidate rationale: {rationale}"
        )

    async def _run_adversarial_verifier(self, counter_query: str) -> dict[str, Any]:
        emit_event(
            "note",
            {"text": "갭을 선언하기 전에 산업에서 이미 널리 쓰인다는 반증을 검색합니다."},
            stage="adversarial_verifier",
            source="system",
        )
        return await self.liner.search_agent(
            [{"role": "user", "content": counter_query}],
            mode="general",
            lang="ko",
            stage="adversarial_verifier",
        )

    async def _run_conditional_deep_research(
        self,
        topic: str,
        gap: dict[str, Any],
        counter_evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not gap["should_deep_research"]:
            reason = "근거 충돌·부족 또는 고임팩트 저확신 조건이 없어 Deep Research를 건너뜁니다."
            emit_event(
                "note",
                {"text": reason},
                stage="conditional_deep_research",
                source="system",
            )
            return {"used": False, "timed_out": False, "reason": reason}

        emit_event(
            "note",
            {"text": "1차 근거가 부족하거나 모순되어 Deep Research로 조건부 승격합니다."},
            stage="conditional_deep_research",
            source="system",
        )
        timeout_s = float(os.environ.get("DEEP_RESEARCH_TIMEOUT_S", "25"))
        research = await self.liner.deep_research(
            [
                {
                    "role": "user",
                    "content": (
                        f"Investigate the application gap for {topic}. Compare academic maturity, "
                        f"industrial adoption, and the following counter-evidence: {counter_evidence}"
                    ),
                }
            ],
            lang="ko",
            timeout_s=timeout_s,
            stage="conditional_deep_research",
        )
        report = _extract_report(research)
        if research.get("timed_out"):
            fallback_reason = "Deep Research 타임아웃 → Search 근거로 잠정 결론을 유지하고 확신도를 낮춥니다."
            emit_event(
                "note",
                {"text": fallback_reason},
                stage="conditional_deep_research",
                source="system",
            )
            return {
                "used": True,
                "timed_out": True,
                "reason": fallback_reason,
                "events_received": len(research.get("events", [])),
                "report": report,
            }
        return {
            "used": True,
            "timed_out": False,
            "reason": "조건부 승격 완료",
            "events_received": len(research.get("events", [])),
            "report": report,
        }

    async def _run_gap_map(self, topic: str, gap: dict[str, Any], *, max_results: int) -> dict[str, Any]:
        emit_event(
            "note",
            {"text": "최종 점수와 근거 요약을 Gap Map 시각화로 전달합니다."},
            stage="gap_map",
            source="system",
        )
        query = (
            f"Application gap comparison for {topic}: academic evidence maturity "
            f"{gap['evidence_maturity']}/100, industrial adoption evidence "
            f"{gap['adoption_evidence']}/100, coverage confidence "
            f"{gap['coverage_confidence']}/100. Label: {gap['gap_label']}."
        )
        visualization = await self.liner.visualize(
            query,
            is_search_context=True,
            max_results=max_results,
            appearance="light",
            stage="gap_map",
        )
        return {
            "requested": True,
            "artifact_received": _has_event_type(visualization, "data-atlas"),
            "events_received": len(visualization.get("events", [])),
        }


async def run_pipeline(topic: str, **kwargs: Any) -> dict[str, Any]:
    """Convenience entry point for the analyze route."""
    return await ResearchPipeline().run(topic, **kwargs)


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
    return {
        "totalCount": sum(response.get("totalCount", len(response.get("results", []))) for response in responses),
        "results": results,
        "searches": responses,
    }


def _flatten_results(responses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for response in responses for item in response.get("results", [])]


def _extract_counter_evidence(result: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = []
    for event in result.get("events", []):
        event_type = event.get("type")
        data = event.get("data") or {}
        if event_type == "data-search-references":
            evidence.extend({"kind": "reference", **item} for item in data.get("references", []))
        elif event_type == "data-search-chunks":
            chunks = data.get("referenceChunks", data.get("reference_chunks", []))
            evidence.extend({"kind": "chunk", **item} for item in chunks)
    return evidence


def _extract_report(result: dict[str, Any]) -> str:
    return "".join(
        event.get("delta", "")
        for event in result.get("events", [])
        if event.get("type") == "text-delta"
    )


def _has_event_type(result: dict[str, Any], event_type: str) -> bool:
    return any(event.get("type") == event_type for event in result.get("events", []))


def _downgrade_for_counter_evidence(gap: dict[str, Any], counter_evidence: list[dict[str, Any]]) -> dict[str, Any]:
    source_keys = {
        item.get("source_url")
        or item.get("url")
        or item.get("source_title")
        or item.get("title")
        for item in counter_evidence
    }
    source_keys.discard(None)
    if len(source_keys) < 2 or gap["gap_label"] not in {"gap_candidate", "weak_gap_candidate"}:
        return gap
    gap["gap_label"] = "weak_gap_candidate"
    gap["rationale"] += " 반증 검색에서 산업 도입 근거가 확인되어 갭 확신도를 한 단계 낮췄습니다."
    return gap
