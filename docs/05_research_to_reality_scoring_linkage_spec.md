# Research-to-Reality Radar
## 증거 구조화·클러스터링·연결 분석·점수 계산 구현 명세

- 문서 상태: **해커톤 MVP 구현 기준 확정안**
- 기준일: 2026-08-01
- 대상 독자: 백엔드 구현 담당, 프론트엔드 구현 담당, 발표 담당
- 관련 문서:
  - `01_전략_기획.md`
  - `02_파이프라인_스펙.md`
  - `03_실행계획.md`
  - `04_실전_파이프라인_전환계획.md`
  - `research_to_reality_scoring_context_2026-08-01.md`

---

# 0. 결론부터

이 계산 로직은 해커톤 MVP 구현을 시작하기에 충분히 구체화됐다. 다만 **가중치와 임계값은 과학적으로 확정된 값이 아니라 초기 기본값**이다. 따라서 코드에서는 전부 설정값으로 분리하고, 실제 검색 결과를 테스트하면서 조정한다.

제품의 핵심 결과는 단순한 점수 3개가 아니다.

> **학술적으로 검증된 적용 형태와 현실에서 확인된 도입 형태를 클러스터로 묶고, 두 클러스터가 어디에서 연결되고 어디에서 끊기는지를 분석한다.**

세 점수는 이 연결 구조를 요약하는 계기판이다.

1. **Evidence Maturity**: 해당 연구 적용 주장이 얼마나 성숙했는가
2. **Adoption Evidence**: 동일하거나 인접한 현실 적용이 얼마나 확인되는가
3. **Coverage Confidence**: 직접 연결이 없다는 결론을 내릴 만큼 검색이 충분했는가

강한 `gap_candidate`는 다음 조건을 모두 충족할 때만 선언한다.

```text
Evidence Maturity 높음
+ Adoption Evidence 낮음
+ Coverage Confidence 충분
```

검색 결과가 없다는 사실은 현실에서 사용되지 않는다는 뜻이 아니다. 사용자에게는 반드시 다음처럼 표현한다.

```text
현재 검색 범위에서는 직접 연결되는 도입 사례를 확인하지 못했다.
```

---

# 1. 문제 정의

## 1.1 기존 방식의 한계

학술 논문 링크와 산업 자료 링크를 각각 나열한 뒤 점수 차이만 보여주면 사용자는 다음을 알 수 없다.

- 어떤 학술 결과가 어떤 산업 사례와 연결되는가
- 기술은 같지만 산업, 사용 사례, 운영 단계가 다른가
- 실제 도입은 있으나 파일럿에서 멈췄는가
- 같은 문제를 산업에서는 다른 기술로 해결하고 있는가
- 도입을 시도했지만 중단된 직접 증거가 있는가
- 추가로 연결 가능성이 높은 인접 사례는 무엇인가

따라서 제품의 본체는 링크 목록이 아니라 **Research-to-Reality 연결 그래프**다.

## 1.2 갭의 분석 단위

주제 전체에 점수 하나를 매기지 않는다. 다음 단위별로 연구 클러스터를 만든다.

```text
기술 × 사용 사례 × 적용 환경 × 기대 효과
```

예시:

```text
연합학습
× 진단모델 공동학습
× 다기관 병원
× 개인정보를 노출하지 않는 협업
```

이 적용 단위마다 산업 도입 클러스터와의 연결을 분석하고 점수를 계산한다.

---

# 2. 핵심 설계 원칙

## 2.1 LLM과 코드의 책임 분리

### LLM이 담당하는 것

- 원문에서 핵심 주장과 근거 문구 추출
- 조직명, 기술명, 사용 사례, 적용 환경, 기대 효과 식별
- 산업 자료가 실제 사용, 파일럿, 운영, 중단을 의미하는지 판단
- 학술 자료가 재현, 종합, 현실 환경 검증, 반례에 해당하는지 분류
- 두 표현이 의미상 같은지 또는 부분적으로 관련되는지 판단
- 애매한 경우 `null`, `unclear`, `uncertain`으로 abstain
- 연결 분석의 자연어 설명 생성
- 잠재 연결 후보와 추가 검증 조건 제안

### 코드가 담당하는 것

- URL, DOI, 정규화 제목의 완전 중복 제거
- 조직명과 기술명 정규화 결과 적용
- 클러스터 ID 생성과 병합
- 동일 사건의 반복 카운트 방지
- 신호 개수 집계
- 가중치, 상한, 임계값 적용
- 세 점수 계산
- `link_type`, `gap_type`, 최종 label 판정
- 후보 정렬과 Deep Research 승격 여부 결정

핵심 원칙:

> **LLM은 증거의 의미를 해석하고, 코드는 증거의 수와 최종 판정을 결정한다.**

## 2.2 연결 분석이 점수보다 먼저다

잘못된 순서:

```text
학술 점수 계산
→ 산업 점수 계산
→ 두 점수 차이를 갭으로 해석
```

확정 순서:

```text
학술 증거 구조화
→ 연구 적용 클러스터 생성
→ 산업 증거 구조화
→ 산업 도입 클러스터 생성
→ 클러스터 간 연결 분석
→ 연결 구조를 기반으로 세 점수 계산
→ label과 gap type 판정
```

## 2.3 확인된 사실과 추론을 분리한다

- `confirmed`: 원문이 직접 뒷받침하는 내용
- `inferred`: 에이전트가 증거를 연결해 추론한 내용
- `unknown`: 현재 자료로 판단할 수 없는 내용

특히 장벽과 잠재 연결은 반드시 다음처럼 분리한다.

```json
{
  "confirmed_barriers": [],
  "inferred_barriers": [],
  "unknown_barrier": true
}
```

---

# 3. 전체 처리 흐름

```text
사용자 입력
  ↓
0. Scope Calibrator
  ↓
1. Scholar Scout
  ↓
학술 EvidenceRecord 추출
  ↓
Research Application Cluster 생성
  ↓
1.5 Vocabulary Bridge
  ↓
2. Adoption Scout
  ↓
산업 EvidenceRecord 추출
  ↓
Adoption Cluster 생성
  ↓
3A. Cluster Link Analyzer
  ↓
3B. Score Calculator
  ↓
3C. Gap Type / Label 판정
  ↓
4. Adversarial Verifier
  ↓
링크와 점수 재계산
  ↓
5. Conditional Deep Research
  ↓
필요한 경우 장벽·충돌 심화 검증
  ↓
6. Gap Map 및 구조화된 최종 응답
```

기존 `Gap Candidate Generator`는 내부적으로 다음 세 부분으로 나눈다.

1. `ClusterLinkAnalyzer`
2. `ScoreCalculator`
3. `GapClassifier`

외부 파이프라인 단계 번호는 그대로 유지해도 된다.

---

# 4. 공통 증거 모델

## 4.1 EvidenceRecord의 의미

EvidenceRecord 하나는 문서 전체가 아니라, **한 출처에서 추출한 점수·클러스터링에 필요한 핵심 주장 한 건**이다.

MVP 복잡도를 통제하기 위해 다음 제한을 둔다.

- 기본: 검색 결과 하나당 가장 관련성이 높은 핵심 주장 한 건
- 예외: 하나의 문서에 서로 다른 조직 또는 서로 다른 사용 사례가 명확히 존재하면 최대 2건까지 분리
- 동일 문장의 표현만 다른 주장은 분리하지 않음

## 4.2 공통 필드

```python
class BaseEvidenceRecord(BaseModel):
    record_id: str
    source_id: str
    source_url: str
    source_title: str
    published_at: str | None

    technology_raw: str | None
    technology_canonical: str | None
    use_case_raw: str | None
    use_case_canonical: str | None
    context_raw: str | None
    context_canonical: str | None
    expected_value_raw: str | None
    expected_value_canonical: str | None

    canonical_claim: str | None
    evidence_span: str
    extraction_confidence: float
```

### 필드 의미

| 필드 | 의미 |
|---|---|
| `technology` | 사용하거나 검증하는 핵심 기술 |
| `use_case` | 기술이 수행하는 업무 또는 기능 |
| `context` | 산업, 조직, 사용자, 장소, 운영 환경 |
| `expected_value` | 정확도, 비용, 속도, 안전, 개인정보 보호 등 기대 효과 |
| `evidence_span` | 원문 안에 실제로 존재하는 근거 구절 |
| `extraction_confidence` | 추출 자체에 대한 LLM 확신도 |

## 4.3 핵심 주장 생성 규칙

기본 형태:

```text
[subject 또는 technology] + [행동/효과] + [use_case/context/value]
```

규칙:

1. 한 주장에 핵심 행동 또는 결과 하나만 둔다.
2. 원문이 직접 말한 범위를 넘어서지 않는다.
3. `evidence_span`은 입력 원문에 실제로 존재해야 한다.
4. 기술, 사용 사례, 환경 중 핵심 필드가 모두 불명확하면 레코드를 생성하지 않는다.
5. 검색 결과 부재를 `does_not_use`로 바꾸지 않는다.
6. `extraction_confidence < 0.55`인 레코드는 점수 계산에서 제외하고 Coverage Confidence에 실패로 반영한다.

---

# 5. 학술 EvidenceRecord

## 5.1 목표

체계적 문헌고찰 수준의 정밀 분류가 아니라, Evidence Maturity 계산에 필요한 최소 신호를 안정적으로 추출한다.

## 5.2 최소 스키마

```python
ResultDirection = Literal[
    "supports",
    "mixed",
    "contradicts",
    "unclear",
]

class AcademicEvidenceRecord(BaseEvidenceRecord):
    record_type: Literal["academic"] = "academic"

    is_replication: bool
    is_synthesis: bool
    is_real_world: bool
    is_counter_evidence: bool
    result_direction: ResultDirection

    institutions: list[str] = []
```

## 5.3 신호 판정 기준

### `is_replication`

기존 결과 또는 기존 방법을 독립적으로 다시 검증하는 연구다.

포함 표현:

- replication
- reproducibility study
- independent validation
- repeated evaluation of a prior claim

제외:

- 단순히 같은 벤치마크를 사용한 새 모델
- 원 연구팀이 동일 데이터를 다시 분석한 경우가 명확한 경우

애매하면 `false`로 두고 `result_direction`만 추출한다.

### `is_synthesis`

여러 연구 결과를 체계적으로 종합하는 연구다.

포함:

- systematic review
- meta-analysis
- evidence synthesis

제외:

- narrative introduction
- 단순 관련 연구 정리

### `is_real_world`

실제 기관, 실제 사용자, 실제 업무 흐름에서 검증한 경우다.

포함:

- 병원, 기업, 정부기관, 실제 현장 배포
- 실제 업무 담당자가 사용한 현장 실험
- 실제 운영 데이터와 업무 흐름을 포함한 전향적 검증

제외:

- 현실 데이터셋만 사용한 오프라인 벤치마크
- 시뮬레이션
- 합성 데이터
- 실험실 사용자 연구만 존재

### `is_counter_evidence`

연구 적용 주장에 대한 실패, 반례, 효과 부재, 일반화 실패를 직접 보고한다.

`result_direction == "contradicts"`이면 일반적으로 `true`다. 다만 문서가 기술 전체가 아니라 매우 제한된 조건만 반박한다면 `mixed`로 둘 수 있다.

## 5.4 결과 방향

| 값 | 의미 |
|---|---|
| `supports` | 연구 적용 주장을 직접 지지 |
| `mixed` | 조건, 데이터, 집단, 지표에 따라 결과가 갈림 |
| `contradicts` | 반대 결과, 성능 저하, 효과 없음이 명확 |
| `unclear` | 검색 스니펫으로 결과 방향을 판단할 수 없음 |

`unclear`는 방향 일관성 계산의 분모에서 제외한다.

## 5.5 학술 레코드 예시

```json
{
  "record_id": "acad_001",
  "source_id": "doi:10.xxxx/example",
  "source_url": "https://...",
  "source_title": "Independent validation of federated learning...",
  "published_at": "2025",
  "technology_raw": "federated learning",
  "technology_canonical": "federated learning",
  "use_case_raw": "joint diagnostic model training",
  "use_case_canonical": "diagnostic model training",
  "context_raw": "multiple hospitals",
  "context_canonical": "multi-hospital healthcare",
  "expected_value_raw": "without sharing raw patient data",
  "expected_value_canonical": "privacy-preserving collaboration",
  "canonical_claim": "연합학습은 다기관 병원에서 환자 원본 데이터를 공유하지 않고 진단 모델을 공동 학습하게 한다.",
  "evidence_span": "...",
  "extraction_confidence": 0.91,
  "is_replication": true,
  "is_synthesis": false,
  "is_real_world": true,
  "is_counter_evidence": false,
  "result_direction": "supports",
  "institutions": ["Hospital A", "University B"]
}
```

---

# 6. 산업 EvidenceRecord

## 6.1 산업 relation

```python
Relation = Literal["uses", "does_not_use"] | None
```

### `uses`

특정 조직의 실제 구현, 시험, 제한 운영, 정식 운영이 원문에 직접 나타난다.

포함:

- 제품 내부에 기술을 실제 구현
- 파일럿 또는 PoC 수행
- 실제 업무에 배포
- 기존 시스템 또는 워크플로우에 통합
- 내부 업무에서 실제 사용

제외:

- 기술 지원 또는 호환 주장
- 도입 계획
- 관심 또는 검토
- 채용공고에 기술명 등장
- 구매 계약만 있고 설치·사용 여부 불명

### `does_not_use`

특정 조직이 사용하지 않거나, 도입을 거절·중단·제거했다는 사실을 원문이 직접 명시한다.

포함:

- pilot ended without adoption
- rejected after evaluation
- discontinued
- removed from production
- prohibited by policy

중요:

```text
검색 결과 없음 ≠ does_not_use
공개 도입 증거가 적음 ≠ does_not_use
```

### `null`

사용 행동을 직접 확인할 수 없거나 subject, technology가 불명확한 경우다.

## 6.2 사용 맥락

```python
UsageContext = Literal[
    "vendor_product_integration",
    "vendor_internal_use",
    "end_user_use",
] | None
```

| 값 | 의미 |
|---|---|
| `vendor_product_integration` | 벤더 제품 또는 서비스가 기술을 실제 구현 |
| `vendor_internal_use` | 기술 공급사가 자기 내부 업무에 기술을 사용 |
| `end_user_use` | 병원, 기업, 정부 등 최종 사용 조직이 본래 업무에 사용 |

제품 구현과 고객 도입을 같은 현실 채택으로 세지 않는다.

## 6.3 도입 단계

```python
AdoptionStage = Literal[
    "pilot",
    "limited_deployment",
    "production",
    "unknown",
] | None
```

| 값 | 판정 기준 |
|---|---|
| `pilot` | PoC, trial, evaluation 등 시험 목적의 실제 사용 |
| `limited_deployment` | 실제 업무에 사용하지만 특정 부서, 지역, 사이트로 제한 |
| `production` | 정규 업무 흐름에 통합되어 반복적·지속적으로 운영 |
| `unknown` | 실제 사용은 명확하지만 단계가 불명 |
| `null` | 제품 구현이거나 relation이 `uses`가 아님 |

보수적 판정:

- `deployed`라는 단어만으로 production을 선언하지 않는다.
- `production-ready`, `enterprise-grade`는 제품 능력 설명이지 도입 증거가 아니다.
- 시험 기간이 길어도 평가 목적이면 pilot이다.

## 6.4 산업 스키마

```python
class AdoptionEvidenceRecord(BaseEvidenceRecord):
    record_type: Literal["adoption"] = "adoption"

    subject_raw: str | None
    subject_canonical: str | None

    relation: Relation
    usage_context: UsageContext
    adoption_stage: AdoptionStage

    deployment_unit: str | None
    project_name: str | None
    event_date: str | None

    explicit_barriers: list[str] = []
```

## 6.5 필드 제약

```python
if relation != "uses":
    usage_context = None
    adoption_stage = None

if usage_context == "vendor_product_integration":
    adoption_stage = None
```

`does_not_use`의 상세 유형은 MVP에서 별도 enum으로 세분화하지 않고, `explicit_barriers`와 `canonical_claim`에 보존한다.

## 6.6 산업 레코드 예시

```json
{
  "record_id": "adopt_001",
  "source_id": "url:https://...",
  "source_url": "https://...",
  "source_title": "Hospital A expands federated learning pilot",
  "published_at": "2025-05",
  "subject_raw": "Hospital A",
  "subject_canonical": "Hospital A",
  "technology_raw": "federated learning platform",
  "technology_canonical": "federated learning",
  "use_case_raw": "training diagnostic models across departments",
  "use_case_canonical": "diagnostic model training",
  "context_raw": "radiology department",
  "context_canonical": "hospital radiology",
  "expected_value_raw": "protect patient data",
  "expected_value_canonical": "privacy-preserving collaboration",
  "canonical_claim": "Hospital A는 영상진단 모델 공동학습에 연합학습을 제한적으로 운영한다.",
  "evidence_span": "...",
  "extraction_confidence": 0.89,
  "relation": "uses",
  "usage_context": "end_user_use",
  "adoption_stage": "limited_deployment",
  "deployment_unit": "radiology department",
  "project_name": null,
  "event_date": "2025-05",
  "explicit_barriers": []
}
```

---

# 7. 정규화와 중복 제거

## 7.1 처리 순서

```text
1. 완전 중복 제거
2. 조직명 정규화
3. 기술명 정규화
4. 사용 사례·환경·기대 효과 정규화
5. 클러스터 후보 버킷 생성
6. 의미적 병합 판정
7. 최종 클러스터 ID 생성
```

## 7.2 완전 중복 제거

우선순위:

1. 동일 DOI
2. canonical URL 동일
3. 제목 정규화 후 동일하며 출판연도 동일
4. `source_url + evidence_span` 동일

정규화 URL에서는 추적 파라미터를 제거한다.

예:

```text
utm_source
utm_campaign
ref
fbclid
```

## 7.3 조직명 정규화

최소 처리:

- 대소문자와 불필요한 법인 접미사 제거
- `Inc.`, `Ltd.`, `Corporation`, `Co.` 정규화
- 알려진 약칭과 정식명 연결
- 병원 네트워크와 개별 병원은 원문이 구분하면 별도 조직으로 유지

예:

```text
IBM Corp. → IBM
Massachusetts General Hospital → Massachusetts General Hospital
MGH → Massachusetts General Hospital
```

LLM이 `subject_canonical`을 제안하되, 실제 그룹키는 코드가 생성한다.

## 7.4 기술명 정규화

Vocabulary Bridge의 결과를 사용한다.

```json
{
  "academic_term": "gradient boosted decision trees",
  "industry_variants": ["XGBoost", "LightGBM", "gradient boosting"],
  "mapping_confidence": 0.92
}
```

기술 관계:

```python
ObjectMatch = Literal[
    "exact",
    "variant",
    "related",
    "unrelated",
    "unclear",
]
```

- `exact`, `variant`: 같은 기술 축으로 클러스터링 가능
- `related`: 직접 병합하지 않고 잠재 연결 분석에만 사용
- `unrelated`: 제외
- `unclear`: Coverage Confidence 감소

---

# 8. 클러스터링

# 8.1 Research Application Cluster

학술 레코드를 다음 공통 적용 단위로 묶는다.

```text
technology_canonical
+ use_case_canonical
+ context_canonical
+ expected_value_canonical
```

## 스키마

```python
class ResearchCluster(BaseModel):
    cluster_id: str

    technology: str
    use_case: str | None
    context: str | None
    expected_value: str | None

    evidence_ids: list[str]
    source_urls: list[str]

    unique_paper_count: int
    replication_count: int
    synthesis_count: int
    real_world_count: int
    supports_count: int
    mixed_count: int
    contradicts_count: int
    unclear_count: int

    evidence_maturity: int | None = None
```

## 병합 규칙

다음이 모두 충족되면 병합한다.

- 기술이 `exact` 또는 `variant`
- 사용 사례가 동일하거나 상하위 관계가 명확
- 적용 환경이 동일하거나 한쪽이 더 구체적인 표현
- 기대 효과가 동일하거나 한쪽이 더 구체적인 표현

다음은 분리한다.

- 같은 기술이라도 사용 사례가 다름
- 같은 사용 사례라도 산업 환경이 실질적으로 다름
- 기대 효과가 정반대 또는 별도 성과 축

예:

```text
연합학습 × 의료영상 진단 × 병원 × 개인정보 보호
연합학습 × 금융 이상탐지 × 은행 × 개인정보 보호
```

기술과 기대 효과는 같지만 사용 사례와 환경이 다르므로 별도 연구 클러스터다.

# 8.2 Adoption Cluster

산업 레코드는 **하나의 실제 도입 사건** 단위로 묶는다.

기본 그룹키:

```text
subject_canonical
+ technology_canonical
+ use_case_canonical
+ context_canonical
+ project_name 또는 deployment_unit
```

## 스키마

```python
class AdoptionCluster(BaseModel):
    cluster_id: str

    subject: str
    technology: str
    use_case: str | None
    context: str | None
    expected_value: str | None

    usage_context: UsageContext
    max_stage_attained: AdoptionStage
    latest_relation: Relation

    deployment_unit: str | None
    project_name: str | None
    first_event_date: str | None
    latest_event_date: str | None

    evidence_ids: list[str]
    source_urls: list[str]
    independent_source_count: int
    explicit_barriers: list[str]
```

## 같은 클러스터로 묶는 사례

- 같은 기업의 같은 파일럿을 여러 기사에서 보도
- 벤더 문서와 고객 보도자료가 같은 고객 도입을 설명
- 같은 프로젝트가 pilot에서 production으로 발전
- 같은 프로젝트가 pilot 후 중단

## 분리하는 사례

- 같은 조직이 서로 다른 부서·업무에 사용
- 같은 조직이 서로 다른 제품 또는 프로젝트에 구현
- 벤더 제품 구현과 고객사의 제품 도입
- 도입 중단 후 별도 신규 프로젝트로 재시작

## 단계 집계

```text
pilot < limited_deployment < production
```

클러스터에는 다음을 함께 보존한다.

- `max_stage_attained`: 역사적으로 도달한 가장 높은 단계
- `latest_relation`: 최신 확인 상태가 uses인지 does_not_use인지

예:

```json
{
  "max_stage_attained": "pilot",
  "latest_relation": "does_not_use"
}
```

이는 “파일럿까지 도달했지만 정식 도입 없이 중단됨”을 의미한다.

## LLM 병합 판정

코드의 하드키로 결정하기 어려운 후보만 LLM에 전달한다.

```python
ClusterDecision = Literal[
    "same_event",
    "different_event",
    "uncertain",
]
```

규칙:

```python
if decision == "same_event" and not hard_conflict:
    merge()
else:
    create_new_cluster()
```

`uncertain`은 병합하지 않는다. 잘못된 병합은 서로 다른 실제 도입을 하나로 뭉개기 때문이다.

---

# 9. 학술 클러스터와 산업 클러스터의 연결 분석

## 9.1 비교 차원

각 연구 클러스터와 산업 클러스터를 네 차원에서 비교한다.

| 차원 | 의미 | 기본 가중치 |
|---|---|---:|
| `technology` | 같은 기술 또는 구현체인가 | 0.40 |
| `use_case` | 같은 문제·업무를 해결하는가 | 0.30 |
| `context` | 같은 산업·조직·현장인가 | 0.20 |
| `expected_value` | 같은 효과를 목표로 하는가 | 0.10 |

차원별 값:

```python
DimensionMatch = Literal[0.0, 0.5, 1.0]
```

- `1.0`: 동일하거나 사실상 같은 의미
- `0.5`: 관련되지만 범위 또는 조건이 다름
- `0.0`: 다르거나 판단 불가

## 9.2 연결 유사도

```python
link_similarity = (
    technology_match * 0.40
    + use_case_match * 0.30
    + context_match * 0.20
    + expected_value_match * 0.10
)
```

가중치는 `scoring_config.py`에서 조정 가능하게 둔다.

## 9.3 연결 유형

```python
LinkType = Literal[
    "direct",
    "partial",
    "blocked",
    "unlinked",
]
```

### `direct`

기술, 사용 사례, 적용 환경이 거의 동일하다.

기본 판정:

```python
technology_match >= 0.5
and link_similarity >= 0.80
and adoption.latest_relation == "uses"
```

예:

```text
연구: 병원 영상진단에 기술 X가 효과적
산업: 병원 영상진단 업무에서 기술 X를 운영 중
```

### `partial`

일부 차원은 연결되지만 핵심 조건이 다르다.

기본 판정:

```python
link_similarity >= 0.45
and direct 조건 미충족
and latest_relation == "uses"
```

예:

- 같은 기술이지만 산업이 다름
- 같은 문제지만 더 좁거나 넓은 사용 사례
- 제품 구현만 존재하고 고객 운영은 없음

### `blocked`

유사한 도입을 시도했으나 중단, 거절, 금지된 직접 증거가 있다.

```python
latest_relation == "does_not_use"
and link_similarity >= 0.45
```

### `unlinked`

검색 범위 안에서 `direct`, `partial`, `blocked`에 해당하는 산업 클러스터가 없다.

`unlinked`는 현실 부재 선언이 아니다. Coverage Confidence가 낮으면 다음처럼 약하게 표현한다.

```text
직접 연결을 확인하지 못했으나 검색 범위가 충분하지 않다.
```

## 9.4 ClusterLink 스키마

```python
class ClusterLink(BaseModel):
    link_id: str
    research_cluster_id: str
    adoption_cluster_id: str | None

    technology_match: float
    use_case_match: float
    context_match: float
    expected_value_match: float
    link_similarity: float

    link_type: LinkType
    matched_on: list[str]
    missing_on: list[str]

    explanation: str
    confidence: float

    evidence_ids: list[str]
```

## 9.5 LLM 출력과 코드 판정

LLM 출력:

```json
{
  "technology_match": 1.0,
  "use_case_match": 1.0,
  "context_match": 0.5,
  "expected_value_match": 1.0,
  "matched_on": ["technology", "use_case", "expected_value"],
  "missing_on": ["context"],
  "explanation": "동일 기술과 업무 목적은 확인되지만 산업 환경이 다르다.",
  "confidence": 0.87
}
```

코드가 `link_similarity`와 `link_type`을 계산한다.

---

# 10. 갭 유형

점수와 별도로 **연결이 끊긴 위치**를 판정한다.

```python
GapType = Literal[
    "no_adoption_link",
    "possible_no_adoption_link",
    "stage_gap",
    "context_gap",
    "technology_substitution",
    "barrier_gap",
    "outcome_gap",
]
```

## 10.1 `no_adoption_link`

```python
if not direct_links and not partial_links and coverage_confidence >= 70:
    gap_types.append("no_adoption_link")
```

## 10.2 `possible_no_adoption_link`

```python
if not direct_links and not partial_links and coverage_confidence < 70:
    gap_types.append("possible_no_adoption_link")
```

## 10.3 `stage_gap`

연결되는 도입은 있지만 정식 운영까지 가지 못한 경우다.

```python
if linked_adoptions and max_stage in {"pilot", "limited_deployment"}:
    gap_types.append("stage_gap")
```

## 10.4 `context_gap`

같은 기술과 사용 사례가 다른 산업 또는 환경에서는 사용되지만 목표 환경에서는 확인되지 않은 경우다.

```python
if (
    technology_match >= 0.5
    and use_case_match >= 0.5
    and context_match == 0.0
):
    gap_types.append("context_gap")
```

## 10.5 `technology_substitution`

동일한 문제와 환경에서 산업은 다른 기술을 실제 운영 중인 경우다.

```python
if (
    technology_match == 0.0
    and use_case_match >= 0.5
    and context_match >= 0.5
    and adoption_stage == "production"
):
    gap_types.append("technology_substitution")
```

이는 “산업이 아무것도 하지 않는다”가 아니라 “다른 해결책을 선택했다”는 의미다.

## 10.6 `barrier_gap`

```python
if blocked_links:
    gap_types.append("barrier_gap")
```

파일럿 중단, 도입 거절, 규제 금지 같은 직접 증거가 있을 때만 적용한다.

## 10.7 `outcome_gap`

기술은 운영 중이지만 연구가 주장한 효과가 실제로 확인되지 않았다고 문서가 직접 명시하는 경우다.

```python
if direct_link and explicit_outcome_mismatch:
    gap_types.append("outcome_gap")
```

단순히 성과 지표가 공개되지 않았다는 이유로 `outcome_gap`을 선언하지 않는다.

---

# 11. Evidence Maturity 계산

## 11.1 목적

연구 클러스터가 얼마나 폭넓고 반복적으로 검증됐는지를 계산한다.

## 11.2 신호와 기본 배점

| 신호 | 최대점수 |
|---|---:|
| 고유 논문 수 | 30 |
| 재현 연구 | 15 |
| 종합 연구·메타분석 | 15 |
| 현실 환경 검증 | 20 |
| 결과 방향 일관성 | 20 |
| 합계 | 100 |

## 11.3 산식

```python
breadth_score = min(unique_paper_count, 5) / 5 * 30
replication_score = min(replication_count, 2) / 2 * 15
synthesis_score = min(synthesis_count, 1) * 15
real_world_score = min(real_world_count, 2) / 2 * 20
```

방향 일관성:

```python
classified_count = supports_count + mixed_count + contradicts_count

if classified_count == 0:
    direction_score = 0
else:
    support_ratio = (
        supports_count + 0.5 * mixed_count
    ) / classified_count
    direction_score = support_ratio * 20
```

최종:

```python
evidence_maturity = round(
    breadth_score
    + replication_score
    + synthesis_score
    + real_world_score
    + direction_score
)
```

## 11.4 해석

| 점수 | 해석 |
|---:|---|
| 0-39 | 근거 부족 또는 초기 연구 |
| 40-69 | 일부 근거가 있으나 성숙하다고 보기 어려움 |
| 70-84 | 비교적 성숙한 근거 |
| 85-100 | 반복·종합·현장 검증이 강한 근거 |

이 구간은 UI 설명용이며 label 임계값은 별도 설정으로 관리한다.

---

# 12. Adoption Evidence 계산

## 12.1 목적

해당 연구 클러스터와 직접 또는 부분적으로 연결되는 실제 산업 도입의 강도와 폭을 계산한다.

## 12.2 기본 점수표

### 최종 사용자 도입

| link type | production | limited | pilot | unknown |
|---|---:|---:|---:|---:|
| `direct` | 25 | 16 | 10 | 7 |
| `partial` | 12 | 8 | 5 | 3 |

### 벤더 내부 사용

| link type | production | limited | pilot | unknown |
|---|---:|---:|---:|---:|
| `direct` | 12 | 9 | 6 | 4 |
| `partial` | 6 | 4 | 3 | 2 |

### 제품 구현

```text
vendor_product_integration: 기본 4점
```

기술과 사용 사례가 거의 무관하면 점수에 포함하지 않는다.

### blocked / unlinked

```text
blocked: 0점
unlinked: 0점
```

`blocked`는 Adoption Evidence를 높이지 않고 `barrier_gap`과 Deep Research 승격 신호로 사용한다.

## 12.3 조직별 중복 방지

같은 조직에 여러 도입 클러스터가 연결될 경우:

1. 가장 강한 클러스터: 100%
2. 두 번째로 강한 독립 사용 사례: 50%
3. 세 번째 이후: 점수에 포함하지 않음
4. 조직별 최대 30점

MVP를 더 단순하게 구현해야 하면 조직별 가장 강한 클러스터 하나만 반영해도 된다.

## 12.4 기본 합산

```python
organization_scores = strongest_scores_per_org
base_score = min(
    sum(sorted(organization_scores, reverse=True)[:3]),
    75,
)
```

## 12.5 조직 다양성 보너스

고유 최종 사용자 조직 수 기준:

| 조직 수 | 보너스 |
|---:|---:|
| 0-1 | 0 |
| 2 | 8 |
| 3 | 15 |
| 4 | 20 |
| 5 이상 | 25 |

```python
adoption_evidence = min(base_score + breadth_bonus, 100)
```

## 12.6 중요한 분리

같은 도입 사건을 다룬 출처가 5개여도 도입 사건은 한 건이다.

```text
출처 수 → cluster confidence / Coverage Confidence
도입 사건 수 → Adoption Evidence
```

`does_not_use`도 실제 사용 점수에서 직접 차감하지 않는다.

```text
adoption_evidence: 실제 사용 증거
non_adoption_evidence: 거절·중단·금지 증거
```

---

# 13. Coverage Confidence 계산

## 13.1 목적

검색 결과에서 직접 연결을 찾지 못했을 때, 그 결론을 얼마나 신뢰할 수 있는지 계산한다.

## 13.2 신호와 배점

| 신호 | 최대점수 |
|---|---:|
| 학술 검색량 | 20 |
| 산업 검색량 | 20 |
| 검색 관점 다양성 | 15 |
| 학술어→산업어 매핑 품질 | 15 |
| 구조화 성공률 | 10 |
| 반증 검색 수행·성공 | 20 |
| 합계 | 100 |

## 13.3 산식

```python
scholar_coverage = min(unique_academic_sources / 5, 1.0) * 20
web_coverage = min(unique_web_sources / 8, 1.0) * 20
```

검색 관점은 다음 세 계열이다.

1. 기술명과 동의어
2. 사용 사례 또는 문제
3. 목표 산업·환경

```python
query_coverage = searched_query_family_count / 3 * 15
mapping_score = vocabulary_mapping_confidence * 15
```

구조화 성공률:

```python
if total_relevant_results == 0:
    extraction_score = 0
else:
    extraction_score = (
        structured_record_count / total_relevant_results
    ) * 10
```

반증 검색:

```python
if not adversarial_search_performed:
    adversarial_score = 0
elif adversarial_search_error_or_timeout:
    adversarial_score = 5
elif adversarial_result_count == 0:
    adversarial_score = 12
elif adversarial_result_count == 1:
    adversarial_score = 16
else:
    adversarial_score = 20
```

최종:

```python
coverage_confidence = round(
    scholar_coverage
    + web_coverage
    + query_coverage
    + mapping_score
    + extraction_score
    + adversarial_score
)
```

## 13.4 해석

| 점수 | 해석 |
|---:|---|
| 0-49 | 결론을 내리기 어려운 검색 범위 |
| 50-69 | 잠정 분석 가능, 강한 부재 판정 금지 |
| 70-84 | 직접 연결 여부를 비교적 신뢰 가능 |
| 85-100 | 다양한 관점과 반증 검색까지 충분히 수행 |

---

# 14. 최종 Label

```python
FinalLabel = Literal[
    "unconfirmed_field",
    "insufficient_evidence",
    "gap_candidate",
    "emerging_adoption",
    "no_gap",
]
```

## 14.1 판정 순서

판정 순서가 중요하다.

```python
if field_not_confirmed:
    label = "unconfirmed_field"

elif coverage_confidence < 50 or evidence_maturity < 40:
    label = "insufficient_evidence"

elif adoption_evidence >= 60 or direct_production_org_count >= 2:
    label = "no_gap"

elif (
    evidence_maturity >= 70
    and adoption_evidence <= 30
    and coverage_confidence >= 70
):
    label = "gap_candidate"

else:
    label = "emerging_adoption"
```

`gap_candidate`를 `emerging_adoption`보다 먼저 검사해야 한다. 파일럿 또는 부분 연결이 있어도 연구 성숙도는 높고 도입 강도는 낮을 수 있기 때문이다.

## 14.2 Label 의미

| label | 의미 |
|---|---|
| `unconfirmed_field` | 기술 또는 분야 자체를 학술 검색에서 확인하지 못함 |
| `insufficient_evidence` | 근거 또는 검색 범위가 부족해 갭 판정 불가 |
| `gap_candidate` | 성숙한 연구에 비해 연결되는 현실 도입이 약하고 검색 범위는 충분함 |
| `emerging_adoption` | 파일럿, 부분 연결, 제한 운영 등 초기 연결이 존재 |
| `no_gap` | 직접 연결되는 실제 운영 사례가 충분함 |

`gap_candidate`는 절대적 현실 부재가 아니라 **추가 조사 우선순위가 높은 적용 갭 후보**다.

---

# 15. 후보 우선순위

여러 연구 클러스터 중 어떤 것을 먼저 보여줄지 정렬하기 위한 점수다.

```python
gap_priority = round(
    evidence_maturity
    * (100 - adoption_evidence) / 100
    * coverage_confidence / 100
)
```

예:

```text
Evidence Maturity = 80
Adoption Evidence = 20
Coverage Confidence = 75

Gap Priority = 80 × 0.8 × 0.75 = 48
```

이 값은 label을 결정하지 않는다. 오직 후보 정렬에 사용한다.

---

# 16. 잠재 연결 후보

## 16.1 목적

직접 연결된 사실과 별개로, 어떤 연구 클러스터가 어떤 산업 사례로 확장될 가능성이 높은지 보여준다.

잠재 연결은 사실이 아니라 추론이므로 `status = "inferred"`로 표시한다.

## 16.2 탐색 패턴

### 같은 기술, 다른 환경

```text
금융에서는 production
의료에서는 pilot 또는 미확인
```

### 같은 문제, 다른 기술

```text
산업은 규칙 기반 시스템으로 같은 문제를 해결 중
학술 기술이 대안이 될 가능성
```

### 같은 기대 효과, 다른 워크플로우

```text
비용 절감 목적은 같지만 실제 통합 방식이 다름
```

## 16.3 후보 생성

`partial` 링크와 유사도가 높은 비연결 후보를 합쳐 최대 2개만 출력한다.

```python
candidate_connections = sorted(
    candidate_links,
    key=lambda link: link.link_similarity,
    reverse=True,
)[:2]
```

## 16.4 출력 스키마

```python
class CandidateConnection(BaseModel):
    research_cluster_id: str
    adoption_cluster_id: str | None

    connection_basis: list[str]
    missing_dimensions: list[str]
    required_validation: list[str]

    explanation: str
    status: Literal["inferred"] = "inferred"
    confidence: float
```

`required_validation`은 “도입해야 한다”는 제안이 아니라, 연결 가능성을 확인하기 위해 필요한 추가 검증을 의미한다.

---

# 17. Deep Research 승격 로직

Deep Research는 기본 엔진이 아니라 최종 감사관이다.

## 17.1 승격 조건

아래 중 하나를 만족하면 승격 후보가 된다.

```python
should_deep_research = any([
    contradicts_count > 0 and evidence_maturity >= 60,
    direct_links_exist and blocked_links_exist,
    gap_priority >= 45 and coverage_confidence < 70,
    high_impact_candidate and barrier_reason_unknown,
])
```

## 17.2 우선순위

1. 학술 결과 상호 충돌
2. 산업 사용과 중단 증거가 동시에 존재
3. 고우선 갭인데 Coverage Confidence가 부족
4. 상위 후보의 장벽 원인을 추가 조사해야 함

## 17.3 타임아웃

- 서프라이즈 태스크: 20-25초
- 안전 데모 입력: 최대 70초
- 타임아웃 시 기존 Search Agent 결과로 잠정 결론
- 실패를 숨기지 않고 Raw Stream에 그대로 노출

Deep Research 실패는 전체 `/api/analyze` 실패로 이어지면 안 된다.

---

# 18. Adversarial Verifier 반영

## 18.1 질의 목적

상위 갭 후보마다 다음 반대 질의를 수행한다.

```text
이 기술이 해당 사용 사례와 환경에서 이미 실제 운영되고 있다는 증거를 찾아라.
```

## 18.2 결과 처리

1. Search Agent 결과를 산업 EvidenceRecord로 다시 구조화
2. 기존 Adoption Cluster에 병합 또는 새 클러스터 생성
3. ClusterLink 재계산
4. Adoption Evidence와 Coverage Confidence 재계산
5. label 재판정

## 18.3 라벨 하향을 단순 규칙으로 처리하지 않는다

기존처럼 “반증 출처 2개면 label 한 단계 하향”만 적용하지 않는다. 반증 자료가 실제로 동일한 사용 사례·환경과 연결되는지 확인한 후 점수와 label을 다시 계산한다.

---

# 19. 파이프라인 단계별 구현 책임

## 19.1 Scope Calibrator

출력:

```json
{
  "status": "focused | broad | niche | unconfirmed",
  "selected_topics": [],
  "target_contexts": [],
  "target_outcomes": []
}
```

너무 넓으면 1-3개 대표 하위 주제로 좁힌다. 사용자 재질문 없이 진행한다.

## 19.2 Scholar Scout

- 하위 주제별 Scholar Search
- 검색 결과마다 AcademicEvidenceRecord 추출
- ResearchCluster 생성
- 각 클러스터 Evidence Maturity 계산

## 19.3 Vocabulary Bridge

각 연구 클러스터의 다음 필드를 산업 검색어로 변환한다.

- technology
- use_case
- context
- expected_value

출력:

```json
{
  "industry_terms": [],
  "query_families": {
    "technology": [],
    "use_case": [],
    "context": []
  },
  "mapping_confidence": 0.0
}
```

## 19.4 Adoption Scout

- 산업 검색어 전체로 Web Search
- AdoptionEvidenceRecord 추출
- AdoptionCluster 생성

## 19.5 Gap Candidate Generator

내부 처리:

```text
ClusterLinkAnalyzer
→ ScoreCalculator
→ GapClassifier
→ CandidateConnectionGenerator
```

## 19.6 Adversarial Verifier

- 상위 후보 1개 또는 최대 2개 반증 검색
- 산업 증거와 링크 재구성

## 19.7 Conditional Deep Research

- 충돌, 장벽, 검색 부족을 심화 조사
- 확인된 사실과 추론을 분리해 반환

## 19.8 Gap Map

Visualization API에는 단순 점수만 보내지 않는다.

포함:

- 세 점수
- label
- 핵심 research cluster
- direct / partial / blocked 링크 수
- gap types
- 확인된 장벽
- 잠재 연결 후보

---

# 20. 백엔드 모듈 구조 권장

```text
backend/
├─ agent_pipeline.py
├─ liner_client.py
├─ openai_agents.py
├─ events.py
│
├─ evidence/
│  ├─ models.py
│  ├─ extract_academic.py
│  ├─ extract_adoption.py
│  ├─ normalize.py
│  └─ deduplicate.py
│
├─ clustering/
│  ├─ research_clusters.py
│  ├─ adoption_clusters.py
│  └─ merge_judgement.py
│
├─ linkage/
│  ├─ match_dimensions.py
│  ├─ link_classifier.py
│  ├─ gap_types.py
│  └─ candidate_connections.py
│
├─ scoring/
│  ├─ evidence_maturity.py
│  ├─ adoption_evidence.py
│  ├─ coverage_confidence.py
│  ├─ labels.py
│  └─ config.py
│
└─ prompts/
   ├─ academic_extraction.md
   ├─ adoption_extraction.md
   ├─ cluster_merge.md
   ├─ cluster_link.md
   └─ candidate_connection.md
```

시간이 부족하면 `evidence_logic.py` 한 파일에 먼저 구현하되 함수 경계는 위 구조를 따른다.

---

# 21. 설정 파일

가중치와 임계값은 하드코딩하지 않는다.

```python
# scoring/config.py

EVIDENCE_WEIGHTS = {
    "breadth": 30,
    "replication": 15,
    "synthesis": 15,
    "real_world": 20,
    "direction": 20,
}

LINK_WEIGHTS = {
    "technology": 0.40,
    "use_case": 0.30,
    "context": 0.20,
    "expected_value": 0.10,
}

LINK_THRESHOLDS = {
    "direct": 0.80,
    "partial": 0.45,
}

LABEL_THRESHOLDS = {
    "min_coverage": 50,
    "min_evidence": 40,
    "gap_evidence": 70,
    "gap_max_adoption": 30,
    "gap_coverage": 70,
    "no_gap_adoption": 60,
    "no_gap_direct_production_orgs": 2,
}
```

테스트 단계에서 이 파일만 수정해 결과를 조정할 수 있어야 한다.

---

# 22. 전체 오케스트레이션 의사코드

```python
async def analyze(topic: str) -> AnalysisResponse:
    scope = await calibrate_scope(topic)

    if scope.status == "unconfirmed":
        return build_unconfirmed_response(topic, scope)

    academic_results = await search_scholar(scope)
    academic_records = await extract_academic_records(academic_results)
    academic_records = deduplicate_academic(academic_records)
    research_clusters = build_research_clusters(academic_records)

    for cluster in research_clusters:
        cluster.evidence_maturity = calculate_evidence_maturity(cluster)

    vocabulary = await build_vocabulary_bridge(research_clusters)

    web_results = await search_web(vocabulary)
    adoption_records = await extract_adoption_records(web_results)
    adoption_records = deduplicate_adoption(adoption_records)
    adoption_clusters = await build_adoption_clusters(adoption_records)

    links = await link_research_to_adoption(
        research_clusters,
        adoption_clusters,
    )

    analyses = []

    for research_cluster in research_clusters:
        cluster_links = links_for(research_cluster, links)

        coverage = calculate_coverage_confidence(
            academic_results=academic_results,
            web_results=web_results,
            vocabulary=vocabulary,
            structured_records=academic_records + adoption_records,
            adversarial=None,
        )

        adoption_score = calculate_adoption_evidence(
            research_cluster,
            cluster_links,
            adoption_clusters,
        )

        gap_types = classify_gap_types(
            research_cluster,
            cluster_links,
            adoption_clusters,
            coverage,
        )

        label = classify_final_label(
            evidence_maturity=research_cluster.evidence_maturity,
            adoption_evidence=adoption_score,
            coverage_confidence=coverage,
            links=cluster_links,
        )

        priority = calculate_gap_priority(
            research_cluster.evidence_maturity,
            adoption_score,
            coverage,
        )

        analyses.append(...)

    top_candidate = select_top_candidate(analyses)

    adversarial = await run_adversarial_verifier(top_candidate)
    merge_adversarial_evidence(adversarial)
    recompute_links_and_scores(top_candidate)

    if should_run_deep_research(top_candidate):
        deep_result = await run_deep_research_with_timeout(top_candidate)
        apply_deep_research_result(top_candidate, deep_result)

    candidate_connections = await generate_candidate_connections(
        top_candidate,
        research_clusters,
        adoption_clusters,
        links,
    )

    visualization = await request_gap_map(...)

    return build_analysis_response(...)
```

---

# 23. API 응답 계약

```json
{
  "topic": "...",
  "scope": {},
  "queries": {
    "scholar": [],
    "adoption": [],
    "counter": "..."
  },

  "research_clusters": [],
  "adoption_clusters": [],
  "links": [],

  "gap_candidates": [
    {
      "research_cluster_id": "research_001",
      "scores": {
        "evidence_maturity": 82,
        "adoption_evidence": 24,
        "coverage_confidence": 81,
        "gap_priority": 50
      },
      "label": "gap_candidate",
      "gap_types": ["context_gap", "stage_gap"],

      "connected_links": [],
      "missing_connections": [],

      "confirmed_barriers": [],
      "inferred_barriers": [],
      "candidate_connections": []
    }
  ],

  "counter_evidence": [],
  "deep_research": {
    "used": false,
    "reason": "...",
    "status": "skipped | completed | timeout | error"
  },
  "visualization": {
    "requested": true,
    "artifact_received": true
  }
}
```

---

# 24. Raw API Stream 이벤트

연결 분석과 계산 과정을 사용자가 볼 수 있어야 한다.

권장 stage:

```text
scope_calibrator
scholar_scout
academic_extraction
research_clustering
vocabulary_bridge
adoption_scout
adoption_extraction
adoption_clustering
cluster_linkage
score_calculation
adversarial_verifier
deep_research
visualization
finalization
```

각 단계는 최소한 다음 이벤트를 남긴다.

```text
note
 tool_call
 tool_result
 error
```

예:

```json
{
  "stage": "cluster_linkage",
  "type": "note",
  "payload": {
    "message": "연구 클러스터 3개와 산업 클러스터 5개를 기술·사용 사례·환경·기대 효과 기준으로 연결합니다."
  }
}
```

```json
{
  "stage": "score_calculation",
  "type": "tool_result",
  "payload": {
    "research_cluster_id": "research_001",
    "evidence_signals": {
      "unique_papers": 5,
      "replications": 2,
      "syntheses": 1,
      "real_world": 1,
      "supports": 4,
      "mixed": 1,
      "contradicts": 0
    },
    "evidence_maturity": 82
  }
}
```

점수만 보내지 말고 **점수를 만든 신호 카운트**를 함께 보낸다.

---

# 25. 프론트엔드 결과 화면

## 25.1 화면 우선순위

1. 최종 판정과 세 점수
2. 분석한 연구 적용 단위
3. 연결된 산업 사례
4. 연결이 끊긴 차원
5. 확인된 장벽
6. 잠재 연결 후보
7. 근거 링크

## 25.2 상단 요약

```text
판정: 적용 갭 후보

근거 성숙도       82
현실 도입 증거    24
검색 커버리지     81

주요 갭: context gap + stage gap
```

## 25.3 연결 분석 카드

### 연결된 부분

```text
학술: 기술 X는 이상탐지 정확도를 개선
현실: 금융기관 3곳에서 실제 운영
연결: direct
```

### 연결이 끊긴 부분

```text
학술: 병원 진단 업무에서 검증
현실: 병원 파일럿 1건, 운영 사례 미확인
연결: stage gap
```

### 인접 연결

```text
동일 기술이 금융권에서는 production
의료 환경으로 이전 가능성 존재
추가 검증: 규제 적합성, 병원 시스템 통합
```

## 25.4 링크 배치

링크는 화면 맨 위 결과가 아니다. 각 판단 아래의 근거로 배치한다.

```text
[연구 근거 3개]
[산업 근거 2개]
[반증 검색 근거 1개]
```

## 25.5 시각화

Gap Map은 최소 다음 노드를 표현한다.

```text
Research Cluster
  ├─ direct → Adoption Cluster
  ├─ partial → Adoption Cluster
  ├─ blocked → Adoption Cluster
  └─ unlinked
```

Visualization API 결과가 예상과 다르면 직접 만든 경량 그래프 또는 카드 레이아웃을 기본 화면으로 사용한다.

---

# 26. 프롬프트 계약

## 26.1 학술 증거 추출 프롬프트 핵심

```text
입력 자료에서 연구 적용 주장과 직접 관련된 핵심 증거만 추출하라.
추론하지 말고 원문이 직접 지지하는 범위만 기록하라.

반드시 technology, use_case, context, expected_value를 가능한 범위에서 분리하라.
검색 스니펫만으로 판단할 수 없으면 null 또는 unclear를 사용하라.

is_replication, is_synthesis, is_real_world, is_counter_evidence를 boolean으로 반환하라.
result_direction은 supports, mixed, contradicts, unclear 중 하나다.
evidence_span은 입력에 실제 존재하는 문자열이어야 한다.
```

## 26.2 산업 증거 추출 프롬프트 핵심

```text
subject는 문서 작성자가 아니라 실제로 기술을 사용하거나 사용하지 않는 조직이다.

실제 구현·시험·운영이 직접 나타나면 relation=uses다.
거절·중단·제거가 직접 나타나면 relation=does_not_use다.
계획, 관심, 지원, 호환, 채용공고만 있으면 relation=null이다.

uses일 때 usage_context와 adoption_stage를 보수적으로 판정하라.
deployed라는 단어만으로 production을 선택하지 마라.
evidence_span은 입력에 실제 존재해야 한다.
```

## 26.3 클러스터 연결 프롬프트 핵심

```text
연구 클러스터와 산업 클러스터를 다음 네 차원에서 비교하라.
technology, use_case, context, expected_value.

각 차원은 1.0, 0.5, 0.0 중 하나다.
1.0은 사실상 동일, 0.5는 관련되지만 조건이 다름, 0.0은 다르거나 확인 불가다.

최종 link_type이나 점수는 생성하지 마라.
matched_on, missing_on, explanation, confidence만 반환하라.
```

최종 점수와 유형은 코드가 계산한다.

---

# 27. 실패와 불확실성 처리

## 27.1 검색 결과 부족

- 억지 클러스터를 생성하지 않는다.
- `insufficient_evidence`로 반환한다.
- 어느 검색군이 부족했는지 표시한다.

## 27.2 구조화 실패

- 해당 결과는 점수에서 제외
- `structured_record_count / total_relevant_results`가 Coverage Confidence에 반영
- 전체 파이프라인은 계속 진행

## 27.3 기술명 매핑 불확실

- `mapping_confidence` 감소
- `related` 기술은 직접 도입 증거로 합치지 않음
- 잠재 연결 후보로만 유지

## 27.4 클러스터 병합 불확실

- `uncertain`이면 분리
- 과대 카운트 가능성은 Coverage Confidence 설명에 기록

## 27.5 Search Agent 또는 Deep Research 타임아웃

- 실패를 Raw Stream에 노출
- 기존 검색 근거로 잠정 결론 반환
- `deep_research.status = "timeout"`
- Coverage Confidence를 필요한 만큼 낮춤

## 27.6 Visualization 실패

- 구조화된 분석 JSON은 정상 반환
- 직접 만든 프론트 카드/그래프로 결과 표시

---

# 28. MVP 범위와 제외 범위

## 28.1 반드시 구현

- 학술·산업 EvidenceRecord 구조화
- 연구 적용 클러스터와 산업 도입 클러스터
- 네 차원의 연결 분석
- direct / partial / blocked / unlinked
- 세 점수 계산
- gap type과 최종 label
- 반증 검색 후 재계산
- 확인된 사실과 추론의 분리
- 사용자 화면에서 연결·단절·잠재 연결 표시

## 28.2 시간 부족 시 축소 가능

- 한 출처 최대 레코드 1건
- 조직별 도입 점수는 가장 강한 클러스터 하나만 반영
- Research Cluster와 Adoption Cluster를 각각 최대 5개로 제한
- 후보 연결은 연구 클러스터당 상위 3개 산업 클러스터만 비교
- 잠재 연결 후보 최대 2개

## 28.3 해커톤 이후로 미룸

- 저자 네트워크 기반 독립 연구팀 판정
- 연구 설계 8종 이상의 세부분류
- 표본 크기와 효과 크기 정밀 추출
- 시간에 따른 도입 생애주기 전체 그래프
- 전사·부서·지점의 계층적 조직 모델
- 자동 임계값 학습
- 통계적 보정 또는 calibration model

---

# 29. 테스트 시나리오

## 29.1 강한 적용 갭

```text
학술 논문 5개 이상
재현 2개
종합 1개
현실 검증 1개
산업 direct link 없음
반증 검색 완료
```

기대:

```text
Evidence Maturity ≥ 70
Adoption Evidence ≤ 30
Coverage Confidence ≥ 70
gap_candidate
```

## 29.2 이미 산업화된 기술

```text
직접 연결되는 production 조직 2개 이상
```

기대:

```text
no_gap
```

## 29.3 파일럿에 머문 기술

```text
학술 근거 높음
직접 연결 pilot 1-2건
production 없음
```

기대:

```text
stage_gap
gap_candidate 또는 emerging_adoption
```

구체 label은 Adoption Evidence와 Coverage Confidence에 따라 결정된다.

## 29.4 다른 산업에서만 도입

```text
기술과 use_case는 동일
목표 context는 다름
```

기대:

```text
partial link
context_gap
```

## 29.5 다른 기술이 문제를 해결 중

```text
use_case와 context 동일
technology 다름
production 존재
```

기대:

```text
technology_substitution
```

## 29.6 도입 중단

```text
pilot 후 rejected 또는 discontinued
```

기대:

```text
blocked link
barrier_gap
Adoption Evidence 가산 없음
```

## 29.7 검색 결과 희소

```text
Scholar 1건
Web 1건
Vocabulary mapping 낮음
```

기대:

```text
insufficient_evidence
```

## 29.8 가짜 또는 확인 불가 분야

기대:

```text
unconfirmed_field
조기 종료
```

---

# 30. 수용 기준

구현 완료는 다음 조건을 모두 충족해야 한다.

## 백엔드

- [ ] `/api/analyze` 입력 1회로 전체 과정 자동 수행
- [ ] AcademicEvidenceRecord가 구조화되어 반환
- [ ] AdoptionEvidenceRecord가 구조화되어 반환
- [ ] ResearchCluster와 AdoptionCluster 생성
- [ ] ClusterLink에 네 차원 점수와 설명 존재
- [ ] 세 점수에 신호별 breakdown 포함
- [ ] gap type과 label이 코드로 계산
- [ ] Adversarial Verifier 결과가 재계산에 반영
- [ ] Deep Research 타임아웃이 전체 실패를 유발하지 않음
- [ ] unknown SSE event를 버리지 않음

## 프론트엔드

- [ ] 세 점수와 label 표시
- [ ] 연구 적용 단위 표시
- [ ] direct / partial / blocked / unlinked를 구분
- [ ] 연결된 부분과 끊긴 부분 표시
- [ ] 확인된 장벽과 추론된 장벽 분리
- [ ] 잠재 연결 후보 표시
- [ ] 각 분석 아래 근거 링크 표시
- [ ] Raw API Stream에 클러스터링·연결·계산 이벤트 표시

## 발표

- [ ] “링크를 나열하는 것이 아니라 연구와 현실을 연결한다”는 메시지 전달
- [ ] 점수가 어떻게 계산됐는지 신호 카운트로 설명 가능
- [ ] 검색 부재를 현실 부재로 단정하지 않는 표현 사용
- [ ] Deep Research 호출 또는 스킵 이유를 설명 가능

---

# 31. 구현 우선순위

현재 시간 제약에서 다음 순서로 구현한다.

1. **Pydantic 데이터 모델 확정**
2. **Scholar/Web 결과 구조화 프롬프트 연결**
3. **간단한 정규화와 중복 제거**
4. **ResearchCluster / AdoptionCluster 생성**
5. **네 차원 연결 분석**
6. **세 점수 계산과 label 판정**
7. **반증 검색 결과 재투입**
8. **프론트 연결·단절 카드 연동**
9. **잠재 연결 후보**
10. **Deep Research와 Visualization 보강**

가장 중요한 완성 기준은 다음이다.

> 사용자가 결과를 보고 “어떤 연구 결과가 현실의 어떤 사례와 연결됐고, 어디에서 연결이 끊겼는지”를 한눈에 이해할 수 있어야 한다.

---

# 32. 최종 한 문장

> Research-to-Reality Radar는 학술 자료와 산업 자료를 각각 요약하는 도구가 아니라, `기술·사용 사례·환경·기대 효과` 단위로 두 세계를 연결하고, 연결된 부분·끊긴 부분·중단된 부분·잠재적으로 연결 가능한 부분을 근거와 함께 판정하는 리서치 에이전트다.
