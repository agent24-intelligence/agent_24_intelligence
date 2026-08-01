"""
임시 스텁: 실제 파이프라인(Liner/OpenAI 연동)이 붙기 전까지 프론트를 개발·테스트하기 위한 것.
실제 파이프라인 코드가 emit_event()를 호출하기 시작하면 이 라우터는 지워도 된다.

/api/demo/run 을 호출하면 6.5단계 파이프라인 흐름을 흉내 낸 이벤트를 순서대로 흘려보낸다.
아래 항목을 의도적으로 포함시켜서 Raw API Stream 화면이 실제로 버텨내는지 확인한다:
- note (판단 근거 로그), tool_call/tool_result
- 순서가 뒤집힌 text/reasoning 이벤트 (기획 문서의 실측 결과 반영)
- 문서에 없는 미상 이벤트 타입 하나 (fallback 렌더링 확인용)
- 타임아웃으로 강등되는 에러 케이스 (실패를 숨기지 않는지 확인용)
"""

import asyncio

from fastapi import APIRouter

from events import emit_event

router = APIRouter()


async def _run_mock_pipeline():
    async def step(delay: float, *args, **kwargs):
        await asyncio.sleep(delay)
        emit_event(*args, **kwargs)

    await step(0.0, "note", {"text": "Scope Calibrator: 입력 주제 범위 판정 시작"}, stage="scope_calibrator", source="openai")
    await step(0.4, "tool_call", {"name": "scope_calibrator", "input": {"topic": "온디바이스 sLLM 양자화"}}, stage="scope_calibrator", source="openai")
    await step(0.5, "tool_result", {"name": "scope_calibrator", "output": {"scope": "적정", "action": "그대로 진행"}}, stage="scope_calibrator", source="openai")

    await step(0.3, "tool_call", {"name": "search", "params": {"mode": "scholar", "query": "on-device sLLM quantization"}}, stage="scholar_scout", source="liner")
    await step(1.2, "tool_result", {"name": "search", "hits": 14, "signals": {"independent_sources": 6, "replications": 2, "recent_counterexamples": 0}}, stage="scholar_scout", source="liner")
    await step(0.2, "note", {"text": "근거 성숙도 82/100 (독립 출처 6, 재현연구 2, 최근 반례 0 — 가중합)"}, stage="scholar_scout", source="system")

    await step(0.4, "note", {"text": "Vocabulary Bridge: 학술 용어 → 업계 동의어 후보 생성"}, stage="vocabulary_bridge", source="openai")
    await step(0.5, "tool_result", {"name": "vocabulary_bridge", "candidates": ["INT4 quantization", "GGUF", "on-device LLM"], "confidence": "high"}, stage="vocabulary_bridge", source="openai")

    await step(0.3, "tool_call", {"name": "search", "params": {"mode": "general", "query": "GGUF on-device LLM 도입 사례"}}, stage="adoption_scout", source="liner")
    await step(1.4, "tool_result", {"name": "search", "hits": 5, "signal_groups_confirmed": "3/6"}, stage="adoption_scout", source="liner")
    await step(0.2, "note", {"text": "공개적으로 확인 가능한 도입 증거 제한적 (검색범위 6개 신호군 중 3개 확인)"}, stage="adoption_scout", source="system")

    await step(0.4, "tool_result", {"name": "gap_candidate_generator", "evidence_maturity": 82, "adoption_evidence": 24, "coverage_confidence": 81}, stage="gap_candidate_generator", source="openai")

    await step(0.3, "tool_call", {"name": "search_agent", "params": {"mode": "general", "query": "이미 산업에서 널리 쓰인다는 증거"}}, stage="adversarial_verifier", source="liner")
    await step(1.0, "tool_result", {"name": "search_agent", "counter_evidence_found": False, "reference_chunks": 3}, stage="adversarial_verifier", source="liner")

    await step(0.3, "note", {"text": "학술/산업 결과 상호 충돌 감지 → Deep Research로 조건부 승격"}, stage="conditional_deep_research", source="system")
    await step(0.2, "tool_call", {"name": "deep_research", "params": {"topic": "온디바이스 sLLM 양자화 적용 갭"}, "timeout_s": 25}, stage="conditional_deep_research", source="liner")
    # 실측: 답변이 먼저 오고, 사고 요약은 나중에 통째로 온다 (문서 기재 순서와 반대)
    await step(0.6, "text-start", {}, stage="conditional_deep_research", source="liner")
    await step(0.3, "text-delta", {"delta": "온디바이스 양자화는 학계에서 성숙했지만"}, stage="conditional_deep_research", source="liner")
    await step(0.3, "text-delta", {"delta": " 산업 도입 사례는 제한적으로 확인됩니다."}, stage="conditional_deep_research", source="liner")
    await step(0.2, "text-end", {}, stage="conditional_deep_research", source="liner")
    # 문서에 없는 이벤트 예시 — Raw Stream이 깨지지 않고 그대로 노출해야 함
    await step(0.3, "data-quickanswer-queries", {"queries": ["GGUF 채택 사례", "INT4 배포 비용"]}, stage="conditional_deep_research", source="liner")
    await step(0.4, "note", {"text": "25초 타임아웃 도달 → 지금까지의 Search Agent 근거로 잠정 결론, 확신도 낮음으로 표시"}, stage="conditional_deep_research", source="system")
    await step(0.1, "error", {"name": "deep_research", "reason": "timeout", "elapsed_s": 25}, stage="conditional_deep_research", source="liner")

    await step(0.3, "tool_call", {"name": "visualization", "params": {"theme": "comparison"}}, stage="gap_map", source="liner")
    await step(1.0, "tool_result", {"name": "visualization", "theme": "comparison", "artifact": "<div>gap map html (생략)</div>"}, stage="gap_map", source="liner")

    await step(0.2, "finish", {"summary": "적용 갭 신뢰도: HIGH (확신도 낮음 표시 포함)"}, stage="gap_map", source="system")


@router.post("/api/demo/run")
async def run_demo():
    asyncio.create_task(_run_mock_pipeline())
    return {"status": "started"}
