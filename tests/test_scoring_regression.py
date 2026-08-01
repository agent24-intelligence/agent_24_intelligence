"""Deterministic regression checks for scoring and adoption evidence gates.

These cases mirror the five consecutive demo queries. They intentionally do
not call Liner or OpenAI: the purpose is to catch scoring, source filtering,
subject extraction, and label-consistency regressions cheaply and repeatably.
"""

from pathlib import Path
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# The tests exercise deterministic helpers only. Keep collection independent
# from the optional OpenAI Agents SDK, which is used only when running agents.
try:
    import agents as _agents  # noqa: F401
except ModuleNotFoundError:
    _agents = types.ModuleType("agents")
    _agents.Agent = type("Agent", (), {})
    _agents.Runner = type("Runner", (), {})
    sys.modules["agents"] = _agents

from agent_pipeline import (  # noqa: E402
    _fallback_adoption_records,
    _is_non_industry_host,
    _looks_like_industry_adoption_result,
    _source_hostname,
    _subject_overlaps_query,
    _trusted_industry_subject,
)
from evidence_logic import (  # noqa: E402
    calculate_adoption_evidence,
    classify_final_label,
    make_cluster_link,
)
from evidence_models import AdoptionCluster, ResearchCluster  # noqa: E402
from input_gate import looks_too_broad_for_demo  # noqa: E402


def _direct_link(*, subject: str, usage_context: str, index: int):
    research = ResearchCluster(
        cluster_id=f"research-{index}",
        technology="tested technology",
        use_case="tested use case",
        context="production context",
        expected_value="tested value",
    )
    adoption = AdoptionCluster(
        cluster_id=f"adoption-{index}",
        subject=subject,
        technology="tested technology",
        use_case="tested use case",
        context="production context",
        expected_value="tested value",
        usage_context=usage_context,
        max_stage_attained="production",
        latest_relation="uses",
    )
    link = make_cluster_link(
        research,
        adoption,
        dimensions={
            "technology": 1.0,
            "use_case": 1.0,
            "context": 1.0,
            "expected_value": 1.0,
        },
        confidence=1.0,
    )
    return link, adoption


FINAL_CASES = [
    pytest.param("LLM 양자화", "no_gap", 68, "end_user_use", 2, id="llm-quantization"),
    pytest.param("드론 이미지 객체 탐지", "emerging_adoption", 30, "vendor_internal_use", 2, id="drone-object-detection"),
    pytest.param("마르크스의 역사적 물질주의", "gap_candidate", 0, None, 0, id="historical-materialism"),
    pytest.param("확산모델 기반 초해상도", "gap_candidate", 0, None, 0, id="diffusion-super-resolution"),
    pytest.param("RAG 파이프라인 캐싱 전략", "gap_candidate", 0, None, 0, id="rag-caching"),
]


@pytest.mark.parametrize("topic, expected_label, expected_adoption, usage_context, link_count", FINAL_CASES)
def test_final_five_query_set_keeps_adoption_score_and_label_consistent(
    topic, expected_label, expected_adoption, usage_context, link_count
):
    links = []
    clusters = {}
    if usage_context:
        for index in range(link_count):
            subject = f"operator-{index}" if topic == "LLM 양자화" else "same operator"
            link, cluster = _direct_link(subject=subject, usage_context=usage_context, index=index)
            links.append(link)
            clusters[cluster.cluster_id] = cluster

    adoption = calculate_adoption_evidence(links, clusters)
    assert adoption["total"] == expected_adoption, topic

    label = classify_final_label(
        field_not_confirmed=False,
        evidence_maturity=80,
        adoption_evidence=adoption["total"],
        coverage_confidence=90,
        direct_production_org_count=adoption["signals"]["direct_production_orgs"],
    )
    assert label == expected_label, topic


WEAK_SOURCE_CASES = [
    pytest.param(
        "연합학습 의료 데이터",
        {
            "url": "https://news.example.com/column/federated-learning",
            "title": "연합학습 의료 데이터 산업 적용 칼럼",
            "snippet": "의료 데이터 적용 전망과 논의를 소개합니다.",
            "query": "연합학습 의료 데이터 산업 적용 사례",
        },
        id="media-column",
    ),
    pytest.param(
        "확산모델 기반 초해상도",
        {
            "url": "https://en.wikipedia.org/wiki/Super-resolution",
            "title": "Diffusion model super-resolution",
            "snippet": "An encyclopedia overview of the method.",
            "query": "확산모델 기반 초해상도 산업 도입 사례",
        },
        id="wikipedia",
    ),
    pytest.param(
        "확산모델 기반 초해상도",
        {
            "url": "https://neurips.cc/virtual/2024/poster/12345",
            "title": "Diffusion model super-resolution poster",
            "snippet": "Conference poster and research results.",
            "query": "확산모델 기반 초해상도 산업 도입 사례",
        },
        id="conference-poster",
    ),
    pytest.param(
        "RAG 파이프라인 캐싱 전략",
        {
            "url": "https://docs.pinecone.io/guides/rag-cache",
            "title": "How to cache a RAG pipeline",
            "snippet": "A guide explaining how to implement caching.",
            "query": "RAG 파이프라인 캐싱 전략 산업 도입 사례",
        },
        id="how-to-guide",
    ),
]


@pytest.mark.parametrize("topic, item", WEAK_SOURCE_CASES)
def test_weak_source_types_do_not_become_industry_adoption_evidence(topic, item):
    """Media, academic, wiki, and how-to pages must not create adoption points."""
    records = _fallback_adoption_records([item], set())

    assert not _looks_like_industry_adoption_result(item), topic
    assert records == [], topic


def test_source_type_gate_rejects_media_and_academic_hosts():
    for url in (
        "https://news.example.com/column/example",
        "https://en.wikipedia.org/wiki/Example",
        "https://neurips.cc/virtual/2024/poster/12345",
    ):
        assert _is_non_industry_host(_source_hostname({"url": url})) is True


def test_subject_extraction_keeps_operator_subject_and_rejects_query_as_subject():
    operator_item = {
        "url": "https://acme.com/case-studies/rag",
        "title": "Acme uses RAG in customer support",
        "snippet": "Acme deployed RAG for customer support operations.",
        "query": "RAG customer support production deployment",
    }
    guide_item = {
        "url": "https://docs.pinecone.io/guides/rag-cache",
        "title": "RAG pipeline caching: how to guide",
        "snippet": "A guide for implementing RAG caching.",
        "query": "RAG pipeline caching strategy",
    }

    assert _trusted_industry_subject(operator_item) == "Acme"
    assert _subject_overlaps_query("RAG pipeline caching", guide_item) is True
    assert _trusted_industry_subject({**operator_item, "url": "https://news.example.com/acme"}) is None


def test_broad_gate_calibrates_ai_without_a_topic_alias_allowlist():
    assert looks_too_broad_for_demo("AI") is True
    assert looks_too_broad_for_demo("LLM 양자화") is False
