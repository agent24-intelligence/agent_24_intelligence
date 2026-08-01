"""Initial research pipeline orchestration.

The OpenAI decision steps and conditional Deep Research promotion will be
attached here after the raw Scholar/Web evidence flow is connected.
"""

from typing import Any

from events import emit_event
from liner_client import LinerClient


class ResearchPipeline:
    """Run the evidence-gathering part of the application-gap pipeline."""

    def __init__(self, liner: LinerClient | None = None):
        self.liner = liner or LinerClient()

    async def run(
        self,
        topic: str,
        *,
        scholar_query: str | None = None,
        adoption_queries: list[str] | None = None,
        max_results: int = 10,
    ) -> dict[str, Any]:
        scholar_query = scholar_query or topic
        adoption_queries = adoption_queries or [topic]

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

        result = {
            "topic": topic,
            "queries": {
                "scholar": scholar_query,
                "adoption": adoption_queries,
            },
            "scholar": scholar,
            "adoption": adoption,
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
