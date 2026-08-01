"""Research pipeline orchestration."""

from typing import Any

from events import emit_event
from liner_client import LinerClient
from openai_agents import ResearchAgents


class ResearchPipeline:
    """Run the evidence-gathering part of the application-gap pipeline."""

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
        emit_event(
            "note",
            {"text": "Scope Calibrator: 입력 주제의 범위를 판정합니다."},
            stage="scope_calibrator",
            source="system",
        )
        scope = await self.agents.scope(topic)
        selected_topic = scope.selected_topics[0]
        query_generation: dict[str, Any]
        if scholar_query:
            query_generation = {
                "query": scholar_query,
                "rationale": "디버깅용 scholar_query를 그대로 사용합니다.",
            }
        else:
            emit_event(
                "note",
                {"text": "Scope 결과를 학술 표준 용어와 배포 맥락이 포함된 Scholar 쿼리로 정제합니다."},
                stage="scholar_scout",
                source="system",
            )
            generated_query = await self.agents.scholar_query(topic, scope)
            scholar_query = generated_query.query
            query_generation = generated_query.model_dump()

        emit_event(
            "note",
            {"text": "학술 근거를 먼저 확인한 뒤 산업 도입 증거를 검색합니다."},
            stage="pipeline",
            source="system",
        )
        emit_event(
            "note",
            {"text": "Scholar Scout: 학술 검색을 시작합니다."},
            stage="scholar_scout",
            source="system",
        )
        scholar = await self.liner.search_scholar(
            scholar_query,
            lang="ko",
            max_results=max_results,
            stage="scholar_scout",
        )

        emit_event(
            "note",
            {"text": "Vocabulary Bridge: 학술 용어를 산업 검색어로 변환합니다."},
            stage="vocabulary_bridge",
            source="system",
        )
        vocabulary = await self.agents.vocabulary_bridge(topic, scholar)
        adoption_queries = adoption_queries or vocabulary.terms

        emit_event(
            "note",
            {"text": "Adoption Scout: 산업 도입 증거 검색을 시작합니다."},
            stage="adoption_scout",
            source="system",
        )
        adoption: list[dict[str, Any]] = []
        for query in adoption_queries:
            adoption.append(
                await self.liner.search_web(
                    query,
                    lang="ko",
                    max_results=max_results,
                    stage="adoption_scout",
                )
            )

        emit_event(
            "note",
            {"text": "Gap Candidate Generator: 학술·산업 근거를 대조합니다."},
            stage="gap_candidate_generator",
            source="system",
        )
        gap_candidate = await self.agents.gap_candidate(topic, scholar, adoption)

        result = {
            "topic": topic,
            "scope": scope.model_dump(),
            "queries": {
                "scholar": scholar_query,
                "adoption": adoption_queries,
            },
            "scholar_query_generation": query_generation,
            "vocabulary": vocabulary.model_dump(),
            "scholar": scholar,
            "adoption": adoption,
            "gap_candidate": gap_candidate.model_dump(),
        }
        emit_event(
            "finish",
            {"topic": topic, "scholar_results": len(scholar.get("results", [])), "adoption_searches": len(adoption)},
            stage="adoption_scout",
            source="system",
        )
        return result


async def run_pipeline(topic: str, **kwargs: Any) -> dict[str, Any]:
    """Convenience entry point for the analyze route."""
    return await ResearchPipeline().run(topic, **kwargs)
