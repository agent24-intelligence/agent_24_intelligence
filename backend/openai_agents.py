"""OpenAI Agents SDK steps used by the research pipeline."""

import asyncio
import json
import os
from typing import Any, Literal

from agents import Agent, Runner
from pydantic import BaseModel, Field

from events import emit_event


SMALL_MODEL = os.environ.get("OPENAI_SMALL_MODEL", "gpt-4o-mini")
LARGE_MODEL = os.environ.get("OPENAI_LARGE_MODEL", "gpt-4o")


class AgentBudgetTimeout(TimeoutError):
    """An intentional per-stage budget expiry, distinct from a provider error."""


class ScopeDecision(BaseModel):
    status: Literal["broad", "focused", "niche", "unconfirmed"]
    selected_topics: list[str] = Field(default_factory=list, max_length=3)
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
        "accepted_recommendation",
    ]
    original_topic: str
    resolved_topic: str
    message: str
    recommendations: list[str] = Field(default_factory=list, max_length=3)


class ScholarQueryResult(BaseModel):
    query: str = Field(min_length=8)
    rationale: str


class QueryFamilies(BaseModel):
    technology: list[str] = Field(default_factory=list, max_length=3)
    use_case: list[str] = Field(default_factory=list, max_length=3)
    context: list[str] = Field(default_factory=list, max_length=3)


class VocabularyBridgeResult(BaseModel):
    terms: list[str] = Field(min_length=1, max_length=3)
    query_families: QueryFamilies = Field(default_factory=QueryFamilies)
    mapping_confidence: float = Field(ge=0, le=1)
    rationale: str


class AcademicExtraction(BaseModel):
    source_index: int = Field(ge=0)
    is_relevant: bool = True
    technology_raw: str | None = None
    technology_canonical: str | None = None
    use_case_raw: str | None = None
    use_case_canonical: str | None = None
    context_raw: str | None = None
    context_canonical: str | None = None
    expected_value_raw: str | None = None
    expected_value_canonical: str | None = None
    canonical_claim: str = ""
    evidence_span: str = ""
    extraction_confidence: float = Field(ge=0, le=1)
    is_replication: bool = False
    is_synthesis: bool = False
    is_real_world: bool = False
    is_counter_evidence: bool = False
    result_direction: Literal["supports", "mixed", "contradicts", "unclear"] = "unclear"
    institutions: list[str] = Field(default_factory=list)


class AcademicExtractionBatch(BaseModel):
    records: list[AcademicExtraction] = Field(default_factory=list)


class AdoptionExtraction(BaseModel):
    source_index: int = Field(ge=0)
    is_relevant: bool = True
    subject_raw: str | None = None
    subject_canonical: str | None = None
    technology_raw: str | None = None
    technology_canonical: str | None = None
    use_case_raw: str | None = None
    use_case_canonical: str | None = None
    context_raw: str | None = None
    context_canonical: str | None = None
    expected_value_raw: str | None = None
    expected_value_canonical: str | None = None
    canonical_claim: str = ""
    evidence_span: str = ""
    extraction_confidence: float = Field(ge=0, le=1)
    relation: Literal["uses", "does_not_use"] | None = None
    usage_context: Literal[
        "vendor_product_integration",
        "vendor_internal_use",
        "end_user_use",
    ] | None = None
    adoption_stage: Literal["pilot", "limited_deployment", "production", "unknown"] | None = None
    deployment_unit: str | None = None
    project_name: str | None = None
    event_date: str | None = None
    explicit_barriers: list[str] = Field(default_factory=list)


class AdoptionExtractionBatch(BaseModel):
    records: list[AdoptionExtraction] = Field(default_factory=list)


class LinkDimensionResult(BaseModel):
    research_cluster_id: str
    adoption_cluster_id: str
    technology_match: Literal[0.0, 0.5, 1.0]
    use_case_match: Literal[0.0, 0.5, 1.0]
    context_match: Literal[0.0, 0.5, 1.0]
    expected_value_match: Literal[0.0, 0.5, 1.0]
    matched_on: list[str] = Field(default_factory=list)
    missing_on: list[str] = Field(default_factory=list)
    explanation: str = ""
    confidence: float = Field(ge=0, le=1)


class LinkDimensionBatch(BaseModel):
    links: list[LinkDimensionResult] = Field(default_factory=list)


class GapNarrativeResult(BaseModel):
    rationale: str
    connected_points: list[str] = Field(default_factory=list, max_length=5)
    gap_points: list[str] = Field(default_factory=list, max_length=5)
    potential_points: list[str] = Field(default_factory=list, max_length=5)
    # potential_points까지는 "이런 지점이 있다"는 관찰이라 사용자가 다음 행동으로 옮기기
    # 어렵다는 피드백이 있었다. gap_points/potential_points를 근거로, 실제로 무엇을 만들면
    # 갭을 메울 수 있는지 실행 가능한 제안까지 한 단계 더 만든다.
    opportunity_suggestions: list[str] = Field(default_factory=list, max_length=3)


class DeepResearchReviewResult(BaseModel):
    confirmed_barriers: list[str] = Field(default_factory=list, max_length=5)
    inferred_barriers: list[str] = Field(default_factory=list, max_length=5)
    explicit_outcome_mismatch: bool = False
    rationale: str = ""


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
                "mapping_confidence를 낮춘다. query_families에는 technology, use_case, context "
                "세 검색 관점별 용어를 넣고, 확인되지 않은 용어는 만들지 않는다. "
                "technology에는 기술·모델 계열, use_case에는 입력 주제와 같은 실제 업무나 제품 기능, "
                "context에는 산업·배포 환경을 넣는다. 단순 상위 개념만 반복하지 말고 산업 도입 "
                "사례 검색에 쓸 수 있는 구체적 표현을 우선한다. 입력이 음악 장르 분류라면 "
                "use_case는 automatic music genre classification 또는 audio genre tagging처럼 "
                "분류 작업 자체를 유지한다. music recommendation, playlist curation, "
                "similar-song retrieval은 인접 사용 사례이므로 use_case를 대체하는 용어로 쓰지 않는다."
            ),
            output_type=VocabularyBridgeResult,
        )
        self.academic_extractor = Agent(
            name="academic_evidence_extractor",
            model=small_model,
            instructions=(
                "입력된 학술 검색 결과 중 관련 있는 결과에서만 가장 관련 높은 적용 주장 하나를 추출한다. "
                "관련 없는 결과는 is_relevant=false 레코드로 반환하지 말고 records에서 생략한다. "
                "반환하는 레코드는 모두 is_relevant=true여야 한다. "
                "원문에 없는 내용을 추론하지 않는다. technology, use_case, context, expected_value를 "
                "가능한 범위에서 분리하고, evidence_span은 입력 title 또는 snippet에 실제 존재하는 "
                "문자열이어야 한다. 핵심 필드가 불명확하면 반환하지 않는다. "
                "replication, synthesis, real-world 여부와 result_direction을 보수적으로 판정한다. "
                "점수, label, gap type은 반환하지 않는다."
            ),
            output_type=AcademicExtractionBatch,
        )
        self.adoption_extractor = Agent(
            name="adoption_evidence_extractor",
            model=small_model,
            instructions=(
                "입력된 산업 검색 결과 중 실제 도입 증거가 있는 결과만 추출한다. "
                "관련 없는 결과는 is_relevant=false 레코드로 반환하지 말고 records에서 생략한다. "
                "반환하는 레코드는 모두 is_relevant=true여야 한다. subject는 문서 작성자가 아니라 "
                "기술을 실제로 사용하거나 사용하지 않는 조직이다. 구현·시험·운영이 직접 나타나면 "
                "relation=uses, 거절·중단·제거·금지가 직접 나타나면 does_not_use, 계획·관심·호환·채용공고만 "
                "있으면 반환하지 않는다. uses일 때 usage_context와 adoption_stage를 보수적으로 "
                "판정하고 deployed라는 단어만으로 production을 선택하지 않는다. evidence_span은 입력에 "
                "실제로 존재해야 한다. 검색 결과 부재를 does_not_use로 바꾸지 않는다. "
                "uses 레코드는 반드시 '누가 썼는지'(구체적 기업·기관·제품·프로젝트)와 "
                "'어디에 썼는지'(업무, 서비스, 현장, 시스템, 고객 적용 맥락)가 함께 보여야 한다. "
                "AI public services, work processes, organizations, society, Marxism처럼 일반 개념을 "
                "subject로 삼지 않는다. 이론·교육·백과·논문·서평·연구방법론 소개는 산업 도입이 아니다. "
                "한국어 결과에서는 도입, 구축, 운영, 현장 적용, 상용화, 납품, 실증, 시범 운영, "
                "관제, 점검, 검사, 탐지 서비스를 실제 사용 신호로 본다. 단순 연구동향, 논문, "
                "정부 과제 소개만 있고 사용 조직이나 적용 현장이 없으면 생략한다. "
                "공식 기술 블로그, 엔지니어링 블로그, 기업 연구소 글, 사례 연구가 자사 시스템의 "
                "production, rollout, serving, online A/B test, customer deployment, live system을 "
                "직접 설명하면 실제 도입 증거로 본다. 추천, 검색, 큐레이션 사례는 장르 분류기가 "
                "실제로 사용됐다는 문장이 없으면 음악 장르 분류의 도입 증거로 추출하지 않는다."
            ),
            output_type=AdoptionExtractionBatch,
        )
        self.cluster_link_agent = Agent(
            name="cluster_link_analyzer",
            model=large_model,
            instructions=(
                "연구 클러스터와 산업 클러스터를 technology, use_case, context, expected_value 네 "
                "차원으로 비교한다. 각 값은 반드시 1.0(동일), 0.5(관련되나 조건이 다름), 0.0(다르거나 "
                "판단 불가) 중 하나다. 최종 link_type, similarity, 점수, label은 반환하지 않는다. "
                "근거가 충분하지 않으면 0.0과 낮은 confidence를 반환한다. "
                "넓은 개념적 유사성만으로 0.5를 주지 않는다. 산업 클러스터에 구체적 사용 주체와 "
                "적용 맥락이 없거나, 연구 이론이 실제 업무/제품/시스템에 쓰였다는 직접 근거가 없으면 "
                "use_case_match와 context_match는 0.0으로 둔다. 철학·사회과학 이론은 특히 엄격하게 "
                "판정하며, 교육·해설·백과·논문·서평 자료를 산업 도입 연결로 보지 않는다. "
                "music genre classification과 music recommendation은 use_case가 다르다. "
                "이 경우 use_case_match는 0.5 이하로 두고 direct 연결로 볼 수 없게 한다. "
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
            output_type=LinkDimensionBatch,
        )
        self.gap_narrator = Agent(
            name="gap_narrator",
            model=large_model,
            instructions=(
                "이미 코드로 계산된 점수·label·link type을 바꾸지 말고, 제공된 evidence id와 cluster 정보를 "
                "바탕으로 짧고 사실적인 설명만 작성한다. connected_points는 실제 direct/partial 연결, "
                "gap_points는 연결되지 않았거나 stage/context가 끊긴 부분, potential_points는 inferred 후보만 "
                "기록한다. 근거가 없으면 빈 배열을 반환한다. 검색 부재를 현실 부재로 단정하지 않는다. "
                "opportunity_suggestions는 gap_points를 근거로 0~3개만 작성한다. 각 항목은 누가, 무엇을, "
                "어떤 대상에게, 어떤 검증 또는 제품 형태로 제공하면 갭을 줄일 수 있는지 한 문장으로 쓴다. "
                "단순 관찰이나 가능성 문장이 아니라 실행 가능한 제안이어야 한다. label이 no_gap, "
                "insufficient_evidence, unconfirmed_field이거나 gap_points가 비어 있으면 빈 배열로 둔다."
            ),
            output_type=GapNarrativeResult,
        )
        self.deep_research_reviewer = Agent(
            name="deep_research_reviewer",
            model=large_model,
            instructions=(
                "Deep Research 보고서에서 원문이 직접 확인하는 장벽과 결과 불일치만 추출한다. "
                "confirmed_barriers와 에이전트의 해석에 불과한 inferred_barriers를 분리한다. "
                "보고서가 직접 성과 불일치를 말하지 않으면 explicit_outcome_mismatch=false로 둔다."
            ),
            output_type=DeepResearchReviewResult,
        )
        self.final_synthesis_writer = Agent(
            name="final_synthesis_writer",
            model=large_model,
            instructions=(
                "너는 학계-산업 적용 갭 분석 결과를 최종 리포트로 정리하는 writer다. "
                "입력 JSON에 있는 점수, label, gap_types, evidence, barriers, suggestions만 사용한다. "
                "점수·라벨·갭 유형을 바꾸거나 새 근거를 지어내지 않는다. 확인되지 않은 원인은 "
                "'추정' 또는 '확인되지 않음'으로 분리한다. 한국어로 작성한다.\n"
                "형식: 짧은 결론 1문단, '근거 대조', '간극 원인', '적용 기회', '신뢰도' 순서. "
                "적용 기회는 누가, 무엇을, 어떤 사용자에게, 어떤 검증 지표로 시작할지 구체적으로 쓴다. "
                "전체는 700~1000자 안팎으로, 과장 수식어와 느낌표 없이 숫자·근거 중심으로 작성한다."
            ),
        )

    async def scope(self, topic: str, *, timeout_s: float | None = None) -> ScopeDecision:
        return await self._run(
            self.scope_agent,
            topic,
            stage="scope_calibrator",
            purpose="scope decision",
            timeout_s=timeout_s,
        )

    async def preflight(self, topic: str, *, timeout_s: float | None = None) -> InputPreflightResult:
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
            timeout_s=timeout_s,
        )

    async def scholar_query(self, topic: str, scope: ScopeDecision, *, timeout_s: float | None = None) -> ScholarQueryResult:
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
            timeout_s=timeout_s,
        )

    async def vocabulary_bridge(self, topic: str, scholar: dict[str, Any], *, timeout_s: float | None = None) -> VocabularyBridgeResult:
        prompt = json.dumps(
            {"topic": topic, "scholar_evidence": _evidence_preview(scholar)},
            ensure_ascii=False,
        )
        return await self._run(
            self.vocabulary_agent,
            prompt,
            stage="vocabulary_bridge",
            purpose="industrial query generation",
            timeout_s=timeout_s,
        )

    async def academic_extract(self, items: list[dict[str, Any]], *, timeout_s: float | None = None) -> AcademicExtractionBatch:
        prompt = json.dumps(
            {"results": _compact_items(items), "task": "Extract structured academic evidence records only from relevant results. Omit irrelevant results."},
            ensure_ascii=False,
        )
        return await self._run(
            self.academic_extractor,
            prompt,
            stage="academic_extraction",
            purpose="academic evidence extraction",
            timeout_s=timeout_s,
        )

    async def adoption_extract(self, items: list[dict[str, Any]], *, timeout_s: float | None = None) -> AdoptionExtractionBatch:
        prompt = json.dumps(
            {"results": _compact_items(items), "task": "Extract structured adoption evidence records only from direct adoption or non-adoption evidence. Omit irrelevant results."},
            ensure_ascii=False,
        )
        return await self._run(
            self.adoption_extractor,
            prompt,
            stage="adoption_extraction",
            purpose="adoption evidence extraction",
            timeout_s=timeout_s,
        )

    async def cluster_link(self, pairs: list[dict[str, Any]], *, timeout_s: float | None = None) -> LinkDimensionBatch:
        prompt = json.dumps(
            {"pairs": pairs, "task": "Compare each research/adoption cluster pair across four dimensions."},
            ensure_ascii=False,
        )
        return await self._run(
            self.cluster_link_agent,
            prompt,
            stage="cluster_linkage",
            purpose="research-to-reality dimension matching",
            timeout_s=timeout_s,
        )

    async def gap_narrative(self, analysis: dict[str, Any], *, timeout_s: float | None = None) -> GapNarrativeResult:
        prompt = json.dumps(analysis, ensure_ascii=False)
        return await self._run(
            self.gap_narrator,
            prompt,
            stage="finalization",
            purpose="structured gap explanation",
            timeout_s=timeout_s,
        )

    async def review_deep_research(self, report: str, *, timeout_s: float | None = None) -> DeepResearchReviewResult:
        return await self._run(
            self.deep_research_reviewer,
            report,
            stage="conditional_deep_research",
            purpose="deep research evidence review",
            timeout_s=timeout_s,
        )

    async def stream_final_synthesis(
        self,
        analysis: dict[str, Any],
        *,
        run_id: str,
        timeout_s: float | None = None,
    ) -> str:
        prompt = json.dumps(analysis, ensure_ascii=False)
        stage = "final_synthesis"
        call_event = emit_event(
            "tool_call",
            {
                "name": self.final_synthesis_writer.name,
                "purpose": "streamed final analysis report",
                "model": self.final_synthesis_writer.model,
                "run_id": run_id,
                "input": prompt,
            },
            stage=stage,
            source="openai",
        )
        chunks: list[str] = []
        emit_event(
            "text-start",
            {"name": self.final_synthesis_writer.name, "call_id": call_event["id"], "run_id": run_id},
            stage=stage,
            source="openai",
        )

        try:
            streamed = Runner.run_streamed(self.final_synthesis_writer, prompt)

            async def consume() -> None:
                async for event in streamed.stream_events():
                    delta = _stream_text_delta(event)
                    if not delta:
                        continue
                    chunks.append(delta)
                    emit_event(
                        "text-delta",
                        {
                            "name": self.final_synthesis_writer.name,
                            "call_id": call_event["id"],
                            "run_id": run_id,
                            "delta": delta,
                        },
                        stage=stage,
                        source="openai",
                    )

            await asyncio.wait_for(consume(), timeout=timeout_s) if timeout_s is not None else await consume()
            text = "".join(chunks)
            if not text and isinstance(streamed.final_output, str):
                text = streamed.final_output
                emit_event(
                    "text-delta",
                    {
                        "name": self.final_synthesis_writer.name,
                        "call_id": call_event["id"],
                        "run_id": run_id,
                        "delta": text,
                    },
                    stage=stage,
                    source="openai",
                )
            emit_event(
                "text-end",
                {
                    "name": self.final_synthesis_writer.name,
                    "call_id": call_event["id"],
                    "run_id": run_id,
                    "text": text,
                },
                stage=stage,
                source="openai",
            )
            emit_event(
                "tool_result",
                {"name": self.final_synthesis_writer.name, "call_id": call_event["id"], "run_id": run_id, "chars": len(text)},
                stage=stage,
                source="openai",
            )
            return text
        except (asyncio.TimeoutError, TimeoutError) as exc:
            text = "".join(chunks)
            emit_event(
                "note",
                {
                    "name": self.final_synthesis_writer.name,
                    "call_id": call_event["id"],
                    "run_id": run_id,
                    "reason": "budget_timeout",
                    "timeout_kind": "budget",
                    "timeout_s": timeout_s,
                },
                stage=stage,
                source="openai",
            )
            emit_event(
                "text-end",
                {
                    "name": self.final_synthesis_writer.name,
                    "call_id": call_event["id"],
                    "run_id": run_id,
                    "text": text,
                    "timed_out": True,
                },
                stage=stage,
                source="openai",
            )
            emit_event(
                "tool_result",
                {
                    "name": self.final_synthesis_writer.name,
                    "call_id": call_event["id"],
                    "run_id": run_id,
                    "timed_out": True,
                    "timeout_kind": "budget",
                    "chars": len(text),
                },
                stage=stage,
                source="openai",
            )
            raise AgentBudgetTimeout(f"{self.final_synthesis_writer.name} exceeded its {timeout_s}s budget") from exc
        except Exception as exc:
            text = "".join(chunks)
            emit_event(
                "error",
                {
                    "name": self.final_synthesis_writer.name,
                    "call_id": call_event["id"],
                    "run_id": run_id,
                    "message": str(exc),
                    "partial_chars": len(text),
                },
                stage=stage,
                source="openai",
            )
            emit_event(
                "text-end",
                {
                    "name": self.final_synthesis_writer.name,
                    "call_id": call_event["id"],
                    "run_id": run_id,
                    "text": text,
                    "error": str(exc),
                },
                stage=stage,
                source="openai",
            )
            raise

    async def _run(
        self,
        agent: Agent,
        prompt: str,
        *,
        stage: str,
        purpose: str,
        timeout_s: float | None = None,
    ) -> Any:
        call_event = emit_event(
            "tool_call",
            {"name": agent.name, "purpose": purpose, "model": agent.model, "input": prompt},
            stage=stage,
            source="openai",
        )
        try:
            run = Runner.run(agent, prompt)
            result = await asyncio.wait_for(run, timeout=timeout_s) if timeout_s is not None else await run
            output = result.final_output
            serialized = output.model_dump() if isinstance(output, BaseModel) else output
        except (asyncio.TimeoutError, TimeoutError) as exc:
            emit_event(
                "note",
                {
                    "name": agent.name,
                    "call_id": call_event["id"],
                    "reason": "budget_timeout",
                    "timeout_kind": "budget",
                    "timeout_s": timeout_s,
                },
                stage=stage,
                source="openai",
            )
            emit_event(
                "tool_result",
                {
                    "name": agent.name,
                    "call_id": call_event["id"],
                    "timed_out": True,
                    "timeout_kind": "budget",
                },
                stage=stage,
                source="openai",
            )
            raise AgentBudgetTimeout(f"{agent.name} exceeded its {timeout_s}s budget") from exc
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


def _compact_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact = []
    for index, item in enumerate(items):
        compact.append(
            {
                "source_index": index,
                "title": _clip_text(item.get("title") or "", 220),
                "snippet": _clip_text(item.get("snippet") or item.get("description") or "", 900),
                "url": _clip_text(item.get("url") or item.get("link") or "", 320),
                "citationCount": item.get("citationCount"),
                "published_at": item.get("published_at") or item.get("publishedAt"),
                "query_family": item.get("query_family"),
            }
        )
    return compact


def _stream_text_delta(event: Any) -> str:
    data = getattr(event, "data", None)
    event_type = getattr(data, "type", None) or getattr(event, "type", None)
    if isinstance(data, dict):
        event_type = data.get("type") or event_type
        delta = data.get("delta")
    else:
        delta = getattr(data, "delta", None)
    if event_type == "response.output_text.delta" and isinstance(delta, str):
        return delta
    return ""


def _clip_text(value: Any, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit].rstrip() + "…"
