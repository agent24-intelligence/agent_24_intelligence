"""OpenAI Agents SDK steps used by the research pipeline."""

import json
import os
from typing import Any, Literal

from agents import Agent, Runner
from pydantic import BaseModel, Field

from events import emit_event


SMALL_MODEL = os.environ.get("OPENAI_SMALL_MODEL", "gpt-4o-mini")
LARGE_MODEL = os.environ.get("OPENAI_LARGE_MODEL", "gpt-4o")


class ScopeDecision(BaseModel):
    status: Literal["broad", "focused", "niche", "unconfirmed"]
    selected_topics: list[str] = Field(min_length=1, max_length=3)
    rationale: str


class VocabularyBridgeResult(BaseModel):
    terms: list[str] = Field(min_length=1, max_length=3)
    mapping_confidence: float = Field(ge=0, le=1)
    rationale: str


class GapCandidateResult(BaseModel):
    evidence_maturity: int = Field(ge=0, le=100)
    adoption_evidence: int = Field(ge=0, le=100)
    coverage_confidence: int = Field(ge=0, le=100)
    gap_label: Literal[
        "gap_candidate",
        "weak_gap_candidate",
        "insufficient_evidence",
        "unconfirmed_field",
        "no_gap",
        "over_adopted",
    ]
    should_deep_research: bool
    rationale: str


class ResearchAgents:
    """Small/large model agents for query and gap-judgement steps."""

    def __init__(self, *, small_model: str = SMALL_MODEL, large_model: str = LARGE_MODEL):
        self.small_model = small_model
        self.large_model = large_model

        self.scope_agent = Agent(
            name="scope_calibrator",
            model=large_model,
            instructions=(
                "입력 주제의 범위를 판정한다. 너무 넓으면 대표 하위 주제 1~3개로 좁히고, "
                "너무 좁으면 인접한 상위 개념을 선택한다. 존재가 의심되면 unconfirmed로 표시한다. "
                "selected_topics는 실제 검색에 사용할 짧은 주제명으로 작성한다."
            ),
            output_type=ScopeDecision,
        )
        self.vocabulary_agent = Agent(
            name="vocabulary_bridge",
            model=small_model,
            instructions=(
                "학술 주제를 산업 검색어로 바꾼다. 제품명, 오픈소스 프로젝트명, 표준 용어 등 "
                "실제 산업에서 쓰이는 동의어를 최대 3개 제시한다. 확실하지 않은 직역은 "
                "mapping_confidence를 낮춘다."
            ),
            output_type=VocabularyBridgeResult,
        )
        self.gap_agent = Agent(
            name="gap_candidate_generator",
            model=large_model,
            instructions=(
                "학술 검색 결과와 산업 검색 결과를 대조해 적용 갭 후보를 판정한다. "
                "연구 성숙도, 산업 도입 증거, 검색 커버리지를 분리해서 평가하고, "
                "근거가 부족하면 억지로 gap_candidate를 만들지 않는다. "
                "should_deep_research는 근거 부족, 모순, 고임팩트 저확신일 때만 true로 한다."
            ),
            output_type=GapCandidateResult,
        )

    async def scope(self, topic: str) -> ScopeDecision:
        return await self._run(
            self.scope_agent,
            topic,
            stage="scope_calibrator",
            purpose="scope decision",
        )

    async def vocabulary_bridge(self, topic: str, scholar: dict[str, Any]) -> VocabularyBridgeResult:
        prompt = json.dumps(
            {"topic": topic, "scholar_evidence": _evidence_preview(scholar)},
            ensure_ascii=False,
        )
        return await self._run(
            self.vocabulary_agent,
            prompt,
            stage="vocabulary_bridge",
            purpose="industrial query generation",
        )

    async def gap_candidate(
        self,
        topic: str,
        scholar: dict[str, Any],
        adoption: list[dict[str, Any]],
    ) -> GapCandidateResult:
        prompt = json.dumps(
            {
                "topic": topic,
                "scholar_evidence": _evidence_preview(scholar),
                "adoption_evidence": [_evidence_preview(item) for item in adoption],
            },
            ensure_ascii=False,
        )
        return await self._run(
            self.gap_agent,
            prompt,
            stage="gap_candidate_generator",
            purpose="gap judgement",
        )

    async def _run(self, agent: Agent, prompt: str, *, stage: str, purpose: str) -> Any:
        call_event = emit_event(
            "tool_call",
            {"name": agent.name, "purpose": purpose, "model": agent.model, "input": prompt},
            stage=stage,
            source="openai",
        )
        try:
            result = await Runner.run(agent, prompt)
            output = result.final_output
            serialized = output.model_dump() if isinstance(output, BaseModel) else output
        except Exception as exc:
            emit_event(
                "error",
                {"name": agent.name, "call_id": call_event["id"], "message": str(exc)},
                stage=stage,
                source="openai",
            )
            raise

        emit_event(
            "tool_result",
            {"name": agent.name, "call_id": call_event["id"], "model": agent.model, "output": serialized},
            stage=stage,
            source="openai",
        )
        return output


def _evidence_preview(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep prompts small while preserving the evidence fields agents need."""
    results = payload.get("results", []) if isinstance(payload, dict) else []
    preview = []
    for item in results[:5]:
        if isinstance(item, dict):
            preview.append(
                {
                    key: item[key]
                    for key in ("title", "snippet", "url", "citationCount")
                    if key in item
                }
            )
    return {"totalCount": payload.get("totalCount", len(results)), "results": preview}
