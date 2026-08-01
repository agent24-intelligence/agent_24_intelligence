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
    # rationale 한 문단만으로는 사용자가 "무엇이 연결되고 무엇이 안 됐는지" 파악하기 어렵다는
    # 피드백이 있어서, 갭 판정 근거를 세 갈래로 구조화해서 같이 받는다. 각 항목은 근거로 쓴
    # 논문/산업 자료를 구체적으로 지칭하는 한두 문장이어야 하며, 비어 있어도 된다(해당 사항이
    # 없을 수 있음).
    connected_points: list[str] = Field(default_factory=list, max_length=5)
    gap_points: list[str] = Field(default_factory=list, max_length=5)
    potential_points: list[str] = Field(default_factory=list, max_length=5)
    # potential_points까지는 "이런 지점이 있다"는 관찰이라 사용자가 다음 행동으로 옮기기
    # 어렵다는 피드백이 있었다. gap_points/potential_points를 근거로, 실제로 무엇을 만들면
    # 갭을 메울 수 있는지 실행 가능한 제안까지 한 단계 더 만든다.
    opportunity_suggestions: list[str] = Field(default_factory=list, max_length=3)


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
                "입력이 기술명이 아니라 '학생 리포트 표절을 자동으로 잡아주는 서비스', "
                "'고객 문의를 대신 응대해주는 챗봇'처럼 산업 아이디어·제품 설명·해결하고 싶은 "
                "문제 상황으로 서술형으로 들어오는 경우도 있다. 이때도 오타 보정과 똑같이 "
                "auto_corrected로 분류하고, resolved_topic에는 그 아이디어의 밑바탕이 되는 "
                "구체적인 기술/방법론 이름을 추출해서 쓴다. 예: '학생 리포트 표절을 자동으로 "
                "잡아주는 서비스' → resolved_topic '텍스트 표절 탐지(plagiarism detection)'. "
                "밑바탕 기술을 특정할 수 없을 만큼 모호한 아이디어는 needs_calibration으로 "
                "분류하고 후보가 될 만한 기술명들을 recommendations로 제시한다.\n"
                "복구할 수 없는 오타, 무의미한 문자열, 말이 안 되는 문장, 확인할 수 없는 "
                "허구적 전제는 rejected로 분류한다. 이때 resolved_topic은 빈 문자열, "
                "recommendations는 빈 배열로 반환하고 message는 반드시 '검색어를 다시 확인해 "
                "주세요.'로 작성한다. 이 문구는 rejected 전용이다.\n"
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
                "should_deep_research는 근거 부족, 모순, 고임팩트 저확신일 때만 true로 한다.\n"
                "evidence_maturity, adoption_evidence, coverage_confidence는 반드시 "
                "0~100 사이의 정수다 (10점 만점이 아니다). 대략 0~20은 근거가 거의 없음, "
                "40~60은 어느 정도 있지만 제한적, 70~90은 풍부하고 명확한 근거, 90 이상은 "
                "매우 강력하고 광범위한 근거를 뜻한다. 예를 들어 관련 논문이 10개 이상 "
                "나오고 다수의 실제 서비스(예: 유명 상용 제품)가 검색되면 evidence_maturity와 "
                "adoption_evidence는 70 이상이어야 한다 — 이런 상황에서 3, 4처럼 낮은 점수를 "
                "주면 명백히 틀린 판정이다. 점수는 실제로 검색된 근거의 양·질과 반드시 "
                "비례해야 하며, rationale의 서술 내용과 모순되지 않아야 한다.\n"
                "rationale과 별개로, 판정 근거를 세 리스트로도 나눠서 준다 (각 항목은 어떤 "
                "논문/산업 자료를 근거로 했는지 구체적으로 알 수 있게 한두 문장으로, 최대 5개씩):\n"
                "- connected_points: 학술 근거와 산업 근거가 실제로 서로 맞아떨어지는 지점. "
                "예: '논문 X의 양자화 기법이 산업 사례 Y에서 그대로 채택됨'.\n"
                "- gap_points: 학술 쪽엔 있지만 산업 검색 결과에서 대응하는 사례를 못 찾은 "
                "지점 — 이게 진짜 '갭'이다. 예: '논문 X가 제안한 방법은 학술적으로는 성숙하지만 "
                "산업 검색 결과 중 이를 프로덕션에 적용했다는 사례가 없음'.\n"
                "- potential_points: 지금은 직접적인 연결 근거가 없지만, 인접 사례나 산업 "
                "동향으로 볼 때 향후 연결될 가능성이 있어 보이는 지점. 예: '산업 근거 Z가 "
                "인접 기술을 도입하고 있어 이 기법도 확장 적용될 여지가 있음'.\n"
                "확실한 근거가 없으면 억지로 채우지 말고 그 리스트를 비워둔다.\n"
                "gap_points/potential_points를 근거로 opportunity_suggestions도 0~3개 만든다. "
                "이건 '~할 여지가 있다' 같은 관찰이 아니라, 누가 무엇을 어떤 대상에게 어떻게 "
                "제공하면 이 갭을 메울 수 있는지 구체적으로 실행 가능한 제안이어야 한다 — "
                "제품/서비스 형태, 타겟 사용자, 어떤 학술 근거를 기반으로 하는지가 한 문장 안에 "
                "드러나야 한다. 나쁜 예(관찰에 그침): '확장 적용될 여지가 있음'. 좋은 예(제안): "
                "'학술 논문의 최신 임베딩 기반 표절 탐지 기법을 아직 상용화하지 않은 소규모 "
                "학술지 대상 검증 서비스로 제공하면 이 갭을 메울 수 있음.' gap_label이 no_gap이거나 "
                "gap_points가 비어 있어 메울 갭 자체가 없으면 opportunity_suggestions도 빈 "
                "배열로 둔다 — 없는 기회를 억지로 만들어내지 않는다."
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
