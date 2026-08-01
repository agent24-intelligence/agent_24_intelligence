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


class InputPreflightResult(BaseModel):
    status: Literal["ready", "auto_corrected", "needs_calibration", "rejected"]
    reason_code: Literal[
        "ready",
        "typo_corrected",
        "too_broad",
        "too_narrow",
        "unrecoverable_typo",
        "gibberish",
        "fictional_or_unverifiable",
    ]
    original_topic: str
    resolved_topic: str
    message: str
    recommendations: list[str] = Field(default_factory=list, max_length=3)


class ScholarQueryResult(BaseModel):
    query: str = Field(min_length=8)
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
        self.input_preflight_agent = Agent(
            name="input_preflight",
            model=small_model,
            instructions=(
                "사용자 입력이 학계-산업 적용 갭 검색의 주제로 사용할 수 있는지 사전 판정한다. "
                "반드시 다음 네 상태 중 하나를 반환한다: ready, auto_corrected, "
                "needs_calibration, rejected.\n"
                "철자·띄어쓰기·키보드 오타처럼 의도가 명확하고 의미가 변하지 않는 경우에만 "
                "auto_corrected로 분류하고 resolved_topic에 올바른 주제를 쓴다. 예: "
                "'거댜언어모델' → '거대 언어 모델'. 애매한 전문용어를 임의로 만들어내거나 "
                "의미를 확장하지 않는다.\n"
                "복구할 수 없는 오타, 무의미한 문자열, 말이 안 되는 문장, 확인할 수 없는 "
                "허구적 전제는 rejected로 분류한다. 이때 resolved_topic은 빈 문자열, "
                "recommendations는 빈 배열로 반환하고 message는 반드시 '추천 검색어가 없어요. "
                "검색어를 다시 확인해 주세요.'로 작성한다. 이 문구는 rejected 전용이다.\n"
                "실제 기술·연구 주제지만 너무 넓거나 좁거나 구체화가 필요한 경우에는 "
                "needs_calibration으로 분류한다. 명확한 주제인 ready와 자동 보정한 "
                "auto_corrected도 포함해 rejected가 아닌 모든 상태에서 사용자가 선택할 수 "
                "있는 자연어 추천 검색어를 1~3개 반드시 생성한다. 질문형이 아닌 기술명도 "
                "유효한 입력으로 인정한다.\n"
                "recommendations는 반드시 학술 검색·산업 검색에 그대로 넣을 수 있는 "
                "'이름이 있는 구체적 기법/방법론'이어야 한다. '적용 사례', '학습 방법론', "
                "'산업별 활용', '~하는 방법' 같은 카테고리 라벨은 절대 추천으로 쓰지 않는다 "
                "— 이런 라벨은 겉보기엔 구체적 같아도 실제로는 여전히 넓은 주제라 갭 판정이 "
                "안 된다. 나쁜 예 (입력 'LLM'): 'LLM의 적용 사례', 'LLM의 학습 방법론', "
                "'LLM의 산업별 활용'. 좋은 예 (입력 'LLM'): 'LLM 환각(hallucination) 탐지', "
                "'온디바이스 sLLM 양자화', 'RAG 파이프라인 캐싱 전략'처럼 그 자체로 검색하면 "
                "관련 논문이 나올 만한 구체적 기법 이름이어야 한다.\n"
                "message 필드는 상태마다 반드시 다르게 쓴다: rejected에서만 위의 고정 문구를 "
                "쓰고, ready/auto_corrected/needs_calibration에서는 그 문구를 절대 재사용하지 "
                "않는다. 대신 recommendations 존재를 전제로 한 긍정적인 안내문을 새로 "
                "작성한다 — 예를 들어 ready/auto_corrected는 '아래 추천 검색어로 더 구체화해 "
                "보시겠어요?', needs_calibration은 '입력하신 주제는 범위가 넓어요(또는 좁아요). "
                "아래 추천 중 하나를 선택하거나 더 구체적으로 입력해 주세요.'처럼 recommendations가 "
                "실제로 있다는 사실과 모순되지 않게 작성한다."
            ),
            output_type=InputPreflightResult,
        )
        self.scholar_query_agent = Agent(
            name="scholar_query_generator",
            model=small_model,
            instructions=(
                "학술 검색용 쿼리 하나를 만든다. 입력 언어를 학술 표준 영어 용어로 바꾸고, "
                "핵심 기술명뿐 아니라 모델 계열·배포 맥락·방법론을 함께 포함한다. "
                "일반 단어 하나만 남는 쿼리를 만들지 않는다. 명확한 오탐 분야가 있을 때만 "
                "HEVC, MIMO, ultrasound처럼 제외어를 추가한다. "
                "예를 들어 '온디바이스 sLLM 양자화'는 'small language model/sLLM', "
                "'on-device/edge/mobile', 'quantization'을 함께 검색해야 한다. "
                "Liner Scholar Search에 그대로 전달할 수 있는 검색어를 반환한다."
            ),
            output_type=ScholarQueryResult,
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

    async def preflight(self, topic: str) -> InputPreflightResult:
        prompt = json.dumps(
            {
                "topic": topic,
                "task": (
                    "Classify this input before any external research. Correct only an obvious "
                    "typo without changing intent, reject inputs that cannot be interpreted, "
                    "and always provide 1-3 user-facing recommendations unless rejected."
                ),
            },
            ensure_ascii=False,
        )
        return await self._run(
            self.input_preflight_agent,
            prompt,
            stage="input_preflight",
            purpose="input validity and recommendation generation",
        )

    async def scholar_query(self, topic: str, scope: ScopeDecision) -> ScholarQueryResult:
        prompt = json.dumps(
            {
                "topic": topic,
                "scope": scope.model_dump(),
                "task": "Create one precise Scholar Search query with technical and deployment anchors.",
            },
            ensure_ascii=False,
        )
        return await self._run(
            self.scholar_query_agent,
            prompt,
            stage="scholar_scout",
            purpose="scholar query generation",
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
