import { useEffect, useRef, useState } from 'react'
import { connectStream } from '../lib/sse'

// 사용자가 실제로 보는 화면. 주제 입력 → 진행 상황(친화적 문구) → 결과(Gap Map).
// Raw API Stream/API 테스트와 분리된, 이 프로젝트의 유일한 "제품" 화면.

const STAGE_LABEL = {
  input_preflight: '입력 확인하는 중',
  scope_calibrator: '주제 범위 확인하는 중',
  scholar_scout: '학술 근거 검색하는 중',
  vocabulary_bridge: '산업 용어로 변환하는 중',
  adoption_scout: '산업 도입 사례 검색하는 중',
  academic_extraction: '학술 근거를 구조화하는 중',
  research_clustering: '연구 적용 단위를 묶는 중',
  adoption_extraction: '산업 도입 근거를 구조화하는 중',
  adoption_clustering: '산업 도입 사건을 묶는 중',
  cluster_linkage: '학술과 산업 사례를 연결하는 중',
  score_calculation: '점수와 갭 유형을 계산하는 중',
  adversarial_verifier: '반증 검토하는 중',
  conditional_deep_research: '심층 조사하는 중',
  finalization: '최종 판정을 정리하는 중',
  final_synthesis: '최종 분석 작성하는 중',
  gap_map: '결과 정리하는 중',
}

// 사용자에게 그대로 보여주는 판정 문구 — 학술 라벨 대신 결과를 바로 이해할 수 있는 말투로.
// 소개 문구("~분석합니다")와 톤을 맞춰서 정중체(합니다체)로 통일.
const LABEL_TEXT = {
  gap_candidate: '아직 산업 현장에 적용되지 않았습니다',
  emerging_adoption: '산업 적용이 일부 확인됐습니다',
  insufficient_evidence: '판단할 근거가 아직 부족합니다',
  unconfirmed_field: '입력한 분야를 다시 확인해주세요',
  no_gap: '이미 산업에 도입되어 있습니다',
}

// 결과 라벨을 의미에 맞는 톤으로 구분한다 (앰버 = 갭 시그널, 그린 = 해소/정리됨,
// 레드 = 반대 방향 경고, 슬레이트 = 판단 보류) — 사용자가 한눈에 결과 성격을 읽도록.
const LABEL_TONE = {
  gap_candidate: 'label-gap',
  emerging_adoption: 'label-weak',
  insufficient_evidence: 'label-neutral',
  unconfirmed_field: 'label-neutral',
  no_gap: 'label-resolved',
}

// 판정 라벨의 색 계열을 점수 카드에도 "선택적으로"(낮은 점수 칸에만) 물려주기 위한 매핑.
// 판단 보류 상태는 색으로 단정 지으면 안 되니 계열 없음(null)으로 둔다.
const LABEL_COLOR_FAMILY = {
  gap_candidate: 'amber',
  emerging_adoption: 'amber',
  insufficient_evidence: null,
  unconfirmed_field: null,
  no_gap: 'green',
}

const LINK_TYPE_TEXT = {
  direct: '직접 연결',
  partial: '부분 연결',
  blocked: '도입 중단 또는 거절',
  unlinked: '직접 연결 미확인',
}

const GAP_TYPE_TEXT = {
  no_adoption_link: '직접 도입 연결 없음',
  possible_no_adoption_link: '도입 연결 가능성은 있으나 검색 범위 부족',
  stage_gap: '파일럿·제한 운영에서 정식 운영으로 이어지지 않음',
  context_gap: '다른 산업·환경에서만 확인됨',
  technology_substitution: '산업은 다른 기술로 같은 문제를 해결 중',
  barrier_gap: '도입 중단·거절·금지 근거 확인',
  outcome_gap: '운영은 확인됐지만 연구 효과와 결과가 다름',
}

const USAGE_CONTEXT_TEXT = {
  vendor_product_integration: '제품·서비스 기능',
  vendor_internal_use: '내부 운영',
  end_user_use: '현장 사용',
}

const ADOPTION_STAGE_TEXT = {
  pilot: '파일럿',
  limited_deployment: '제한 운영',
  production: '정식 운영',
  unknown: '단계 미확인',
}

// 입력창 예시 — 매번 하나를 랜덤으로 보여준다. 전부 "학술 연구는 있는데 산업 도입은
// 불확실한" 기술/방법론 예시로만 구성 (파이프라인이 실제로 다루는 범위와 일치시킴).
const TOPIC_EXAMPLES = [
  'LLM 환각(hallucination) 탐지',
  '온디바이스 sLLM 양자화',
  '확산모델 기반 초해상도',
  'RAG 파이프라인 캐싱 전략',
  '연합학습(federated learning)',
  '그래프 뉴럴넷 기반 추천시스템',
]

function ArrowIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="19" x2="12" y2="5" />
      <polyline points="6 11 12 5 18 11" />
    </svg>
  )
}

// "이런 기회가 있습니다" 섹션용 반짝임 아이콘.
function SparkIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 2l2.2 6.6L21 11l-6.8 2.4L12 20l-2.2-6.6L3 11l6.8-2.4L12 2z" />
    </svg>
  )
}

// 중단 버튼 아이콘 — X 대신 모서리가 둥근 빈 사각형(정지 버튼의 부드러운 버전)으로.
function StopIcon() {
  return (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
      <rect x="3" y="3" width="18" height="18" rx="5" />
    </svg>
  )
}

function WarningIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  )
}

// 빠른 모드 토글 아이콘 — 별도 라벨/스위치 대신 입력창 옆 아이콘 버튼 하나로 축소.
function BoltIcon() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="13 2 4 14 11 14 10 22 20 10 13 10 13 2" />
    </svg>
  )
}

// 다크/라이트 전환 버튼 아이콘 — 지금이 다크면 "누르면 밝아진다"는 뜻으로 해,
// 지금이 라이트면 "누르면 어두워진다"는 뜻으로 달을 보여준다.
function SunIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="4.5" />
      <line x1="12" y1="1.5" x2="12" y2="4" />
      <line x1="12" y1="20" x2="12" y2="22.5" />
      <line x1="1.5" y1="12" x2="4" y2="12" />
      <line x1="20" y1="12" x2="22.5" y2="12" />
      <line x1="4.5" y1="4.5" x2="6.2" y2="6.2" />
      <line x1="17.8" y1="17.8" x2="19.5" y2="19.5" />
      <line x1="4.5" y1="19.5" x2="6.2" y2="17.8" />
      <line x1="17.8" y1="6.2" x2="19.5" y2="4.5" />
    </svg>
  )
}

function MoonIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a6.8 6.8 0 0 0 10.5 10.5Z" />
    </svg>
  )
}

// result.scholar = { totalCount, results: [...], searches: [...] } — 학술 검색으로 찾은
// 논문 목록. 항목마다 title/url/citationCount 등이 있을 수도 없을 수도 있어 방어적으로 읽는다.
function scholarItems(result) {
  return Array.isArray(result?.scholar?.results) ? result.scholar.results : []
}

// result.adoption은 쿼리별 응답 객체의 배열이라(각각 { results: [...] }), 평탄화해서
// 하나의 목록으로 만든다.
function adoptionItems(result) {
  if (!Array.isArray(result?.adoption)) return []
  return result.adoption.flatMap((resp) => (Array.isArray(resp?.results) ? resp.results : []))
}

function adoptionEvidenceRecords(result) {
  if (!Array.isArray(result?.adoption_evidence)) return []
  return result.adoption_evidence.filter((item) => item?.relation === 'uses')
}

function resultList(result, key) {
  if (Array.isArray(result?.[key])) return result[key]
  if (Array.isArray(result?.gap_candidate?.[key])) return result.gap_candidate[key]
  return []
}

function parseSynthesisMarkdown(text) {
  const lines = String(text || '').split(/\r?\n/)
  const blocks = []
  let paragraph = []

  function flushParagraph() {
    const value = paragraph.join(' ').trim()
    if (value) blocks.push({ type: 'paragraph', text: value })
    paragraph = []
  }

  for (const rawLine of lines) {
    const line = rawLine.trim()
    if (!line) {
      flushParagraph()
      continue
    }
    const heading = line.match(/^#{2,4}\s+(.+)$/)
    if (heading) {
      flushParagraph()
      blocks.push({ type: 'heading', text: heading[1].trim() })
      continue
    }
    paragraph.push(line)
  }
  flushParagraph()
  return blocks
}

function FinalSynthesisText({ text, streaming }) {
  const blocks = parseSynthesisMarkdown(text)
  if (blocks.length === 0) {
    return (
      <p className="final-synthesis-placeholder">
        분석 글 작성 중{streaming && <span className="final-synthesis-cursor" />}
      </p>
    )
  }
  return (
    <>
      {blocks.map((block, index) =>
        block.type === 'heading' ? (
          <h4 key={index} className="final-synthesis-section-title">
            {block.text}
          </h4>
        ) : (
          <p key={index} className="final-synthesis-paragraph">
            {block.text}
            {streaming && index === blocks.length - 1 && <span className="final-synthesis-cursor" />}
          </p>
        ),
      )}
    </>
  )
}

// 점수(0~100)를 명암 강도 클래스로 매핑 — 무채색 베이스 안에서 값이 클수록 진하게.
function scoreTone(value) {
  if (typeof value !== 'number') return ''
  if (value >= 67) return 'score-high'
  if (value >= 34) return 'score-mid'
  return 'score-low'
}

// 판정 색(앰버/그린)을 점수 카드 전체가 아니라 "낮은 점수" 칸에만 선택적으로 물려준다 —
// 그 칸이 지금 판정의 근거가 되는 숫자이기 때문. 중/고점 칸은 그대로 무채색 유지.
function scoreClass(value, label) {
  const tone = scoreTone(value)
  if (tone !== 'score-low') return tone
  const family = LABEL_COLOR_FAMILY[label]
  return family ? `${tone} tone-${family}` : tone
}

// 검색엔진 결과처럼 링크 밑에 출처 도메인을 보여주기 위한 호스트명 추출.
function hostnameOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return null
  }
}

// 검색엔진 결과 카드처럼: 제목(링크) → 출처 도메인 → 스니펫(요약) 순서로 보여준다.
function EvidenceItem({ item, showCitation }) {
  const hostname = item.url ? hostnameOf(item.url) : null
  return (
    <li className="evidence-item">
      <div className="evidence-item-head">
        {item.url ? (
          <a href={item.url} target="_blank" rel="noreferrer">
            {item.title || item.url}
          </a>
        ) : (
          <span className="evidence-item-title">{item.title || '제목 없음'}</span>
        )}
        {showCitation && typeof item.citationCount === 'number' && (
          <span className="evidence-meta">인용 {item.citationCount}회</span>
        )}
      </div>
      {hostname && <div className="evidence-item-source">{hostname}</div>}
      {item.snippet && <p className="evidence-item-snippet">{item.snippet}</p>}
    </li>
  )
}

// 갭 판정 근거를 세 갈래로 나눠 보여준다 — "그냥 링크 목록"이 아니라 "무엇이 연결됐고,
// 무엇이 안 됐고(진짜 갭), 무엇이 더 연결될 여지가 있는지" 사용자가 바로 알 수 있게.
function ConnectionGroup({ tone, title, items }) {
  return (
    <div className={`connection-group tone-${tone}`}>
      <h3 className="connection-group-title">{title}</h3>
      <ul className="connection-list">
        {items.map((text, i) => (
          <li key={i}>{text}</li>
        ))}
      </ul>
    </div>
  )
}

function StructuredLinks({ links }) {
  if (!Array.isArray(links) || links.length === 0) return null
  const visible = links.filter((link) => link.adoption_cluster_id || link.link_type !== 'unlinked')
  if (visible.length === 0) return null
  return (
    <div className="structured-links">
      <h3 className="connection-group-title">연구·산업 연결</h3>
      <ul className="structured-link-list">
        {visible.map((link) => (
          <li key={link.link_id} className={`structured-link structured-link-${link.link_type}`}>
            <span className="structured-link-type">{LINK_TYPE_TEXT[link.link_type] || link.link_type}</span>
            <span className="structured-link-score">유사도 {Math.round((link.link_similarity || 0) * 100)}%</span>
            {link.explanation && <span className="structured-link-explanation">{link.explanation}</span>}
          </li>
        ))}
      </ul>
    </div>
  )
}

function AdoptionUseEvidence({ records }) {
  const visible = Array.isArray(records) ? records.slice(0, 5) : []
  return (
    <div className={`adoption-use-evidence ${visible.length === 0 ? 'adoption-use-evidence-empty' : ''}`}>
      <h3 className="connection-group-title">명시적으로 확인된 사용 근거</h3>
      {visible.length === 0 ? (
        <p className="adoption-use-empty-text">
          사용 주체와 적용 맥락이 함께 확인된 산업 도입 근거는 없습니다.
        </p>
      ) : (
        <ul className="adoption-use-list">
          {visible.map((item) => {
            const subject = item.subject_raw || item.subject_canonical || '사용 주체 미확인'
            const locus =
              item.project_name ||
              item.deployment_unit ||
              item.use_case_raw ||
              item.context_raw ||
              '적용 맥락 미확인'
            const stage = ADOPTION_STAGE_TEXT[item.adoption_stage] || '단계 미확인'
            const context = USAGE_CONTEXT_TEXT[item.usage_context] || '맥락 미확인'
            return (
              <li key={item.record_id || `${subject}-${item.evidence_span}`} className="adoption-use-item">
                <div className="adoption-use-meta">
                  <span>사용 주체: {subject}</span>
                  <span>적용 위치: {locus}</span>
                  <span>{context} · {stage}</span>
                </div>
                {item.evidence_span && <p className="adoption-use-quote">{item.evidence_span}</p>}
                {item.source_url && (
                  <a className="adoption-use-source" href={item.source_url} target="_blank" rel="noreferrer">
                    {item.source_title || item.source_url}
                  </a>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

const EVIDENCE_PAGE_SIZE = 5

// 학술/산업 근거 목록을 5개씩 페이지로 끊어서 보여준다. "나머지 다 보기"로 한 번에
// 왕창 펼치는 대신, 필요한 사람만 다음 페이지로 넘겨보게 해서 화면이 갑자기 안 길어짐.
function EvidenceGroup({ title, items, showCitation, page, onPageChange }) {
  const totalPages = Math.ceil(items.length / EVIDENCE_PAGE_SIZE)
  const pageItems = items.slice(page * EVIDENCE_PAGE_SIZE, (page + 1) * EVIDENCE_PAGE_SIZE)
  return (
    <div className="evidence-group">
      <h3 className="evidence-group-title">
        {title} ({items.length}개)
      </h3>
      <ul className="evidence-list">
        {pageItems.map((item, i) => (
          <EvidenceItem key={item.url || item.title || i} item={item} showCitation={showCitation} />
        ))}
      </ul>
      {totalPages > 1 && (
        <div className="evidence-pager">
          <button type="button" disabled={page === 0} onClick={() => onPageChange(page - 1)} aria-label="이전 페이지">
            <img src="/logo-filled-half-left.png" alt="" className="pager-logo-icon" />
          </button>
          <span className="evidence-pager-status">
            {page + 1} / {totalPages}
          </span>
          <button
            type="button"
            disabled={page >= totalPages - 1}
            onClick={() => onPageChange(page + 1)}
            aria-label="다음 페이지"
          >
            <img src="/logo-filled-half-right.png" alt="" className="pager-logo-icon" />
          </button>
        </div>
      )}
    </div>
  )
}

// 결과가 길어져서 아래로 많이 스크롤됐을 때만 "맨 위로" 버튼을 보여준다.
function useScrollPastTop(threshold = 400) {
  const [past, setPast] = useState(false)
  useEffect(() => {
    function onScroll() {
      setPast(window.scrollY > threshold)
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [threshold])
  return past
}

// 다크/라이트 전환. <html data-theme="..">에 반영해서 index.css의 :root(다크)와
// [data-theme='light'] 토큰 세트가 자동으로 갈아끼워지게 하고, 선택은
// localStorage에 저장해 새로고침해도 유지되게 한다.
function useTheme() {
  const [theme, setTheme] = useState(() => {
    if (typeof window === 'undefined') return 'dark'
    return window.localStorage.getItem('bridge-agent-theme') || 'dark'
  })
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    window.localStorage.setItem('bridge-agent-theme', theme)
  }, [theme])
  return [theme, setTheme]
}

export default function UserView() {
  const [topicExample] = useState(() => TOPIC_EXAMPLES[Math.floor(Math.random() * TOPIC_EXAMPLES.length)])
  const [topic, setTopic] = useState('')
  const [fastMode, setFastMode] = useState(false)
  const [fastModeNotice, setFastModeNotice] = useState(null) // 빠른 모드 버튼 누르면 잠깐 떴다 사라지는 상태 메시지
  const [fastModeNoticeKey, setFastModeNoticeKey] = useState(0) // 연속 클릭 시 애니메이션을 처음부터 다시 재생시키기 위한 키
  const fastModeNoticeTimer = useRef(null)
  const [status, setStatus] = useState('idle') // idle | running | done | error
  const [stage, setStage] = useState(null)
  const [result, setResult] = useState(null)
  const [finalSynthesis, setFinalSynthesis] = useState(null)
  const [errorMsg, setErrorMsg] = useState(null)
  const [liveSuggestions, setLiveSuggestions] = useState([])
  const [liveGuidance, setLiveGuidance] = useState(null) // 추천이 하나도 없을 때(rejected) 보여줄 안내 문구
  const [evidencePage, setEvidencePage] = useState({ scholar: 0, adoption: 0 })
  const [searchNote, setSearchNote] = useState(null) // { mode, query } — 지금 이 순간 실제로 던지고 있는 검색어
  const [lastAnalyzedTopic, setLastAnalyzedTopic] = useState(null) // 직전에 실제로 돌린 주제 — 그대로 재요청하는 걸 막는 데 씀
  const disconnectRef = useRef(null)
  const abortRef = useRef(null)
  const suggestAbortRef = useRef(null)
  const selectedSuggestionRef = useRef(null)
  const resultRunIdRef = useRef(null)
  const synthesisBuffersRef = useRef({})
  const confirmedBarriers = resultList(result, 'confirmed_barriers')
  const inferredBarriers = resultList(result, 'inferred_barriers')

  function applyFinalSynthesisEvent(event) {
    if (event.stage !== 'final_synthesis') return
    const runId = event.payload?.run_id
    if (!runId) return
    const current = synthesisBuffersRef.current[runId] || { runId, status: 'streaming', text: '' }
    let next = current

    if (event.type === 'text-start') {
      next = { runId, status: 'streaming', text: '' }
    } else if (event.type === 'text-delta') {
      next = { ...current, status: 'streaming', text: `${current.text || ''}${event.payload?.delta || ''}` }
    } else if (event.type === 'text-end') {
      const text = event.payload?.text && event.payload.text.length >= (current.text || '').length
        ? event.payload.text
        : current.text || ''
      next = {
        ...current,
        status: event.payload?.error ? 'error' : event.payload?.timed_out ? 'timeout' : 'complete',
        text,
      }
    } else if (event.type === 'error') {
      next = { ...current, status: 'error', error: event.payload?.message || '최종 분석 생성 실패' }
    } else {
      return
    }

    synthesisBuffersRef.current = { ...synthesisBuffersRef.current, [runId]: next }
    if (resultRunIdRef.current === runId) {
      setFinalSynthesis(next)
    }
  }

  useEffect(() => {
    disconnectRef.current = connectStream((event) => {
      if (event.stage) setStage(event.stage)
      applyFinalSynthesisEvent(event)
      // 검색어 문구는 단계가 바뀌어도 지우지 않고 마지막 값을 그대로 둔다 — "결과 정리하는
      // 중" 같은 뒷 단계에서도 방금 어떤 검색어로 찾았는지 맥락이 이어져 보이게.
      // (event.payload.body.query가 빈 문자열이면 이 조건 자체가 false라 절대 '' 그대로
      // 보여주는 일은 없다.)
      if (event.type === 'tool_call' && event.payload?.name === 'search' && event.payload?.body?.query) {
        setSearchNote({ mode: event.payload.mode, query: event.payload.body.query })
      }
    })
    return () => disconnectRef.current?.()
  }, [])

  // 제출 전, 타이핑하는 동안 가볍게 /api/suggestions로 추천 검색어를 미리 보여준다.
  // 입력을 멈추고 잠깐(350ms) 있어야 호출해서, 한 글자씩 칠 때마다 요청이 나가진 않는다.
  useEffect(() => {
    // 'running'일 때만 막는다 — 이전엔 'idle'이 아니면(=done/error) 다 막아서, 결과를
    // 보고 있는 채로 입력을 고치면 추천도 안 뜨고 그렇다고 결과도 안 지워지는 어중간한
    // 상태였다. 이제 결과는 그대로 화면에 남겨두면서 새로 타이핑하는 동안 추천이 뜬다.
    if (status === 'running' || (status === 'done' && result?.status !== 'completed') || topic.trim().length < 2) {
      setLiveSuggestions([])
      setLiveGuidance(null)
      // 이미 날아간 요청이 있으면 취소한다 — 안 그러면 입력을 지운 뒤에도 그 요청이
      // 뒤늦게 응답으로 돌아와서 지웠던 안내 문구("검색어를 다시 확인해 주세요")를
      // 되살려버린다. clearTimeout은 아직 안 나간 타이머만 막고, 이미 fetch가 나간
      // 건 여기서 abort로 직접 끊어줘야 한다.
      suggestAbortRef.current?.abort()
      return
    }

    const timer = setTimeout(async () => {
      suggestAbortRef.current?.abort()
      const controller = new AbortController()
      suggestAbortRef.current = controller
      try {
        const res = await fetch('/api/suggestions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ topic }),
          signal: controller.signal,
        })
        const data = await res.json().catch(() => null)
        // 응답을 기다리는 사이에 더 최신 입력으로 새 요청이 이미 나갔다면 이 응답은 버린다.
        // (기다리는 중 아무것도 안 쳤으면 suggestAbortRef는 여전히 이 controller를 가리키고
        // 있어서 정상적으로 반영된다 — "가만히 기다리면 안 뜰 수도 있는" 문제는 아니다.)
        if (suggestAbortRef.current === controller) {
          const recs = Array.isArray(data?.recommendations) ? data.recommendations : []
          setLiveSuggestions(recs)
          // 추천이 하나도 없으면 조용히 넘어가지 않고, 제출 전에도 뭐가 문제인지 미리 알려준다
          // (버튼 눌러서 결과 화면까지 가야 알게 되는 것보다 지금 바로 아는 게 낫다는 피드백).
          setLiveGuidance(recs.length === 0 && data?.status === 'rejected' ? data.message : null)
        }
      } catch {
        // 보조 기능이라 실패해도 조용히 무시한다 — 사용자가 그냥 계속 타이핑하면 됨.
      }
    }, 350)

    return () => clearTimeout(timer)
  }, [topic, status, result?.status])

  async function runAnalyze(e, topicOverride, options = {}) {
    e?.preventDefault()
    const topicToRun = topicOverride ?? topic
    if (!topicToRun.trim()) return
    const selectedSuggestion = selectedSuggestionRef.current?.trim()
    const acceptedRecommendation =
      Boolean(options.acceptedRecommendation || (selectedSuggestion && topicToRun.trim() === selectedSuggestion))
    // 결과가 이미 떠 있는 상태에서 입력을 안 고치고 그대로 화살표를 또 누르면, 방금과
    // 똑같은 요청을 API 비용 들여가며 한 번 더 돌리게 된다. 직전에 성공한 주제와
    // 완전히 같으면 그냥 지금 보이는 결과를 유지하고 재요청은 건너뛴다.
    if (status === 'done' && topicToRun.trim() === lastAnalyzedTopic) return
    if (topicOverride) setTopic(topicOverride)
    setStatus('running')
    setResult(null)
    setFinalSynthesis(null)
    setErrorMsg(null)
    setStage(null)
    setSearchNote(null)
    setLiveSuggestions([])
    setLiveGuidance(null)
    setEvidencePage({ scholar: 0, adoption: 0 })
    resultRunIdRef.current = null
    synthesisBuffersRef.current = {}

    const controller = new AbortController()
    abortRef.current = controller

    try {
      const res = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          topic: topicToRun,
          max_results: 10,
          fast_mode: fastMode,
          accepted_recommendation: acceptedRecommendation,
        }),
        signal: controller.signal,
      })

      // 서버가 예외를 못 잡고 그대로 죽으면 JSON이 아니라 순수 텍스트 500이 온다.
      // res.json()이 그 경우 SyntaxError를 던지는데, 그걸 그대로 사용자에게 보여주지 않는다.
      let data
      try {
        data = await res.json()
      } catch {
        throw new Error(
          res.ok
            ? '서버 응답을 처리하지 못했습니다. 잠시 후 다시 시도해주세요.'
            : `서버 오류가 발생했습니다 (${res.status}). 잠시 후 다시 시도해주세요.`,
        )
      }

      if (!res.ok) {
        // 백엔드 에러 응답 shape이 { detail } (FastAPI 기본/검증 에러)이거나
        // { error, message } (파이프라인 예외 핸들러)일 수 있어서 둘 다 본다.
        const detail = Array.isArray(data?.detail)
          ? data.detail.map((d) => d.msg || JSON.stringify(d)).join(', ')
          : data?.detail || data?.message
        throw new Error(detail || `요청이 실패했습니다 (${res.status}). 잠시 후 다시 시도해주세요.`)
      }

      resultRunIdRef.current = data.run_id || null
      setResult(data)
      setFinalSynthesis(data.run_id ? synthesisBuffersRef.current[data.run_id] || data.final_synthesis || null : null)
      setStatus('done')
      setLastAnalyzedTopic(topicToRun.trim())
    } catch (err) {
      if (err.name === 'AbortError') {
        setStatus('idle')
        setStage(null)
        return
      }
      setErrorMsg(err instanceof Error ? err.message : '알 수 없는 오류가 발생했습니다. 잠시 후 다시 시도해주세요.')
      setStatus('error')
    } finally {
      abortRef.current = null
    }
  }

  function stopAnalyze() {
    abortRef.current?.abort()
  }

  // 사전 검사에서 온 추천 검색어 칩을 클릭하면 그 주제로 바로 다시 분석을 돌린다.
  function runWithSuggestion(rec) {
    selectedSuggestionRef.current = rec
    runAnalyze(null, rec, { acceptedRecommendation: true })
  }

  // 타이핑 중 뜬 실시간 추천은 아직 제출 전이라, 클릭하면 입력창만 채우고
  // 사용자가 직접 확인 후 제출하도록 둔다 (바로 분석을 돌리지 않음).
  function applySuggestion(rec) {
    selectedSuggestionRef.current = rec
    setTopic(rec)
    setLiveSuggestions([])
    setLiveGuidance(null)
  }

  const showScrollTop = useScrollPastTop()
  const [theme, setTheme] = useTheme()
  const showPreflightGuidance = status === 'done' && result && result.status !== 'completed'
  const preflightGuidanceMessage =
    result?.status === 'needs_calibration'
      ? '입력하신 주제는 범위가 넓어요. 아래 추천 중 하나를 선택하거나 더 구체적으로 입력해 주세요.'
      : result?.recommendations?.length > 0 && result?.message?.includes('검색어를 다시 확인해')
        ? '입력하신 주제로 아래 추천 검색어를 만들었어요. 하나를 선택해 보세요.'
        : result?.message
  const finalSynthesisStatus = finalSynthesis?.status || result?.final_synthesis?.status
  const finalSynthesisText = finalSynthesis?.text || result?.final_synthesis?.text || ''
  const showFinalSynthesis = Boolean(result?.final_synthesis || finalSynthesis)

  return (
    <div className={`user-view${status === 'idle' ? ' is-centered' : ''}`}>
      <button
        type="button"
        className="theme-toggle-btn"
        onClick={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
        aria-label={theme === 'dark' ? '라이트 모드로 전환' : '다크 모드로 전환'}
        title={theme === 'dark' ? '라이트 모드로 전환' : '다크 모드로 전환'}
      >
        {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
      </button>
      {showScrollTop && (
        <button
          type="button"
          className="scroll-top-btn"
          onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
          aria-label="맨 위로"
        >
          <ArrowIcon />
        </button>
      )}
      <div className="user-view-intro">
        <div className="user-view-title-wrap">
          <img src="/logo-filled.png" alt="" className="ghost-symbol" aria-hidden="true" />
          <h1>Bridge Agent</h1>
        </div>
        <p>
          학술 연구와 산업 도입 현황을 비교해 아직 적용되지 않은 격차를 찾습니다.
          <br />
          연구 주제나 산업 아이디어를 입력하면, AI가 관련 근거와 함께 분석 결과를 보여드립니다.
        </p>
      </div>

      <form className="user-view-form" onSubmit={runAnalyze}>
        {/* 별도 줄/라벨/스위치 대신 입력창 옆 아이콘 버튼 하나로 — 폼의 일부처럼 보이게
            해서 "설정 UI"가 튀지 않고 다른 아이콘 버튼들과 같은 언어로 자리잡게 한다. */}
        <div className="user-view-fast-wrap">
          {/* key를 매번 바꿔서 연속으로 빠르게 눌러도 DOM이 새로 마운트되고 애니메이션이
              처음부터 재생되게 한다 — 텍스트만 바뀌면 이미 진행 중인 CSS 애니메이션이
              재시작되지 않아 클릭 속도를 못 따라오는 문제가 있었다. */}
          {fastModeNotice && (
            <div key={fastModeNoticeKey} className="user-view-fast-toast">
              {fastModeNotice}
            </div>
          )}
          <button
            type="button"
            className={`user-view-fast-btn ${fastMode ? 'is-on' : ''}`}
            onClick={() => {
              setFastMode((value) => {
                const next = !value
                setFastModeNotice(next ? '빠른 모드 켜짐' : '빠른 모드 꺼짐')
                setFastModeNoticeKey((k) => k + 1)
                clearTimeout(fastModeNoticeTimer.current)
                fastModeNoticeTimer.current = setTimeout(() => setFastModeNotice(null), 1600)
                return next
              })
              setLastAnalyzedTopic(null)
            }}
            aria-pressed={fastMode}
            disabled={status === 'running'}
            title={fastMode ? '빠른 모드 켜짐 — 단계별 시간 제한 적용' : '빠른 모드 꺼짐 — 시간 제한 없이 전체 분석'}
          >
            <BoltIcon />
          </button>
        </div>
        <input
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder={`예: ${topicExample}`}
          disabled={status === 'running'}
        />
        {status === 'running' ? (
          <button
            type="button"
            className="user-view-icon-btn is-stop"
            onClick={stopAnalyze}
            aria-label="분석 중단"
            title="중단"
          >
            <StopIcon />
          </button>
        ) : (
          <button
            type="submit"
            className="user-view-icon-btn"
            disabled={!topic.trim() || (status === 'done' && topic.trim() === lastAnalyzedTopic)}
            aria-label="분석 시작"
            title={status === 'done' && topic.trim() === lastAnalyzedTopic ? '이미 이 주제로 분석했어요' : '분석 시작'}
          >
            <ArrowIcon />
          </button>
        )}
      </form>

      {status !== 'running' && !showPreflightGuidance && liveSuggestions.length > 0 && (
        <div className={`user-view-live-suggestions${status === 'done' && result ? ' has-stale-result' : ''}`}>
          <span className="live-suggestions-label">추천 검색어</span>
          <div className="user-view-suggestions">
            {liveSuggestions.map((rec) => (
              <button key={rec} type="button" className="suggestion-chip" onClick={() => applySuggestion(rec)}>
                {rec}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* 제출도 하기 전에 이 검색어로는 안 될 것 같다는 걸 미리 알려준다 — 버튼 눌러서
          결과 화면까지 가야 알게 되는 것보다 지금 바로 아는 게 낫다. */}
      {status !== 'running' && !showPreflightGuidance && liveSuggestions.length === 0 && liveGuidance && (
        <div className="user-view-live-guidance">{liveGuidance}</div>
      )}

      {status === 'running' && (
        <div className="user-view-progress">
          <div className="progress-main-row">
            <img src="/logo-filled.png" alt="" className="spinner" />
            <span>{stage ? STAGE_LABEL[stage] || stage : '준비하는 중'}</span>
          </div>
          {/* 검색엔진처럼 지금 실제로 뭘 검색 중인지 보여준다 — 추상적인 단계 이름보다
              구체적인 검색어를 보여주는 게 "돌아가고 있다"는 신뢰를 훨씬 잘 줌 */}
          {searchNote && <div className="progress-search-note">‘{searchNote.query}’로 검색하겠습니다</div>}
        </div>
      )}

      {status === 'error' && (
        <div className="user-view-error">
          <WarningIcon />
          <span>{errorMsg}</span>
        </div>
      )}

      {/* 사전 검사에서 rejected/needs_calibration으로 걸러지면 파이프라인 자체가 안 돌고
          여기서 끝난다 — 점수/라벨이 없는 완전히 다른 모양의 결과라 따로 렌더링한다. */}
      {showPreflightGuidance && (
        <div className="user-view-guidance">
          <p className="user-view-guidance-message">{preflightGuidanceMessage}</p>
          {result.recommendations?.length > 0 && (
            <div className="user-view-suggestions">
              {result.recommendations.map((rec) => (
                <button key={rec} type="button" className="suggestion-chip" onClick={() => runWithSuggestion(rec)}>
                  {rec}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {status === 'done' && result && result.status === 'completed' && (
        <div className="user-view-result">
          {result.preflight?.status === 'auto_corrected' ? (
            // 검색엔진의 "이 검색어에 대한 결과가 없어 다음으로 표시합니다" 안내와 같은
            // 익숙한 패턴 — 카드/배지보다 이게 훨씬 신뢰가 가는 관용구라 그대로 따름.
            // 이 문구 자체가 이미 어떤 주제로 검색했는지 알려주고 있어서 아래 별도 표시는 생략.
            <p className="user-view-corrected-note">
              <span>‘{result.input_topic}’에 대한 검색결과가 없어 다음에 대한 결과를 표시합니다:</span>{' '}
              <strong className="corrected-note-term">{result.topic}</strong>
            </p>
          ) : (
            // 결과를 띄운 채로 입력창을 다시 고칠 수 있게 해놔서, 지금 보이는 결과가
            // 정확히 어떤 검색어에 대한 건지 표시해줘야 헷갈리지 않는다.
            result.topic && <p className="user-view-topic-note">‘{result.topic}’ 검색 결과입니다.</p>
          )}

          {result.analysis_status === 'partial' && (
            <p className="user-view-partial-note">
              시간 예산 안에서 확보된 근거로 잠정 판정했습니다. 아래 점수와 근거는 추가 검색 전 상태입니다.
            </p>
          )}

          <div className={`user-view-label ${LABEL_TONE[result.label] || 'label-neutral'}`}>
            {LABEL_TEXT[result.label] || result.label}
          </div>

          {/* 라벨 바로 아래에 짧은 태그로 갭 종류를 붙여서, 스코어를 읽기 전에
              "무슨 종류의 갭인지"부터 한눈에 들어오게 한다. */}
          {result.gap_types?.length > 0 && (
            <div className="gap-type-summary">
              <span className="gap-type-summary-label">주요 갭</span>
              <div className="gap-type-list">
                {result.gap_types.map((gapType) => (
                  <span key={gapType} className="gap-type-item">
                    {GAP_TYPE_TEXT[gapType] || gapType}
                  </span>
                ))}
              </div>
            </div>
          )}

          <div className="user-view-scores">
            <div className={`score-card ${scoreClass(result.scores?.evidence_maturity, result.label)}`}>
              <span className="score-label">검색된 연구 근거는 얼마나 충분한가</span>
              <span className={`score-value ${scoreClass(result.scores?.evidence_maturity, result.label)}`}>
                {typeof result.scores?.evidence_maturity === 'number' ? `${result.scores.evidence_maturity}%` : '-'}
              </span>
            </div>
            <div className={`score-card ${scoreClass(result.scores?.adoption_evidence, result.label)}`}>
              <span className="score-label">실제로 쓰이고 있나</span>
              <span className={`score-value ${scoreClass(result.scores?.adoption_evidence, result.label)}`}>
                {typeof result.scores?.adoption_evidence === 'number' ? `${result.scores.adoption_evidence}%` : '-'}
              </span>
            </div>
            <div className={`score-card ${scoreClass(result.scores?.coverage_confidence, result.label)}`}>
              <span className="score-label">판정 신뢰도</span>
              <span className={`score-value ${scoreClass(result.scores?.coverage_confidence, result.label)}`}>
                {typeof result.scores?.coverage_confidence === 'number' ? `${result.scores.coverage_confidence}%` : '-'}
              </span>
            </div>
          </div>

          {/* 예전엔 이 자리에 클라이언트에서 다시 조합한 요약 문장(judgmentSummary)이
              따로 떠서 아래 rationale/연계 카드와 사실상 같은 내용을 다른 말로 두 번
              반복했다 — 백엔드가 실제로 근거로 쓴 rationale 하나만 그대로 보여준다. */}
          {result.rationale && <p className="judgment-summary">{result.rationale}</p>}

          {/* 판정 설명(라벨/점수/rationale)과 그 아래 보조 설명 묶음을 구분선으로 나눈다 —
              전부 텍스트/카드가 이어 붙어있으면 어디까지가 "판정"이고 어디부터가
              "부가 설명"인지 눈으로 구분이 잘 안 됐다. */}
          {(result.vocabulary?.rationale ||
            result.connected_points?.length > 0 ||
            result.gap_points?.length > 0 ||
            result.potential_points?.length > 0 ||
            confirmedBarriers.length > 0 ||
            inferredBarriers.length > 0 ||
            result.opportunity_suggestions?.length > 0) && <div className="section-divider" />}

          {result.vocabulary?.rationale && (
            <div className="user-view-bridge">
              <h3 className="bridge-title">학술 용어를 산업 검색어로 이렇게 변환했습니다</h3>
              {result.vocabulary.terms?.length > 0 && (
                <div className="bridge-terms">
                  {result.vocabulary.terms.map((term) => (
                    <span key={term} className="bridge-term-chip">
                      {term}
                    </span>
                  ))}
                </div>
              )}
              <p className="bridge-rationale">{result.vocabulary.rationale}</p>
            </div>
          )}

          {/* 결과 화면의 핵심 — "무엇이 연결됐고, 무엇이 안 됐고, 확인된 장벽은 무엇인지".
              StructuredLinks는 검증용 기술 디테일로 보고 맨 아래 "계산 근거"에 둔다. */}
          {(result.connected_points?.length > 0 ||
            result.gap_points?.length > 0 ||
            result.potential_points?.length > 0 ||
            confirmedBarriers.length > 0 ||
            inferredBarriers.length > 0) && (
            <div className="user-view-connections">
              {result.connected_points?.length > 0 && (
                <ConnectionGroup tone="connected" title="연계된 근거" items={result.connected_points} />
              )}
              {result.gap_points?.length > 0 && (
                <ConnectionGroup tone="gap" title="연계 안 된 부분 (GAP)" items={result.gap_points} />
              )}
              {confirmedBarriers.length > 0 && (
                <ConnectionGroup tone="gap" title="확인된 장벽" items={confirmedBarriers} />
              )}
              {inferredBarriers.length > 0 && (
                <ConnectionGroup tone="potential" title="추정 장벽" items={inferredBarriers} />
              )}
              {result.potential_points?.length > 0 && (
                <ConnectionGroup
                  tone="potential"
                  title="추가로 연계될 여지가 있는 지점"
                  items={result.potential_points}
                />
              )}
            </div>
          )}

          <AdoptionUseEvidence records={adoptionEvidenceRecords(result)} />

          {/* opportunity_suggestions: 백엔드 finalization에서 계산된 갭 포인트를 바탕으로
              실행 가능한 제안이 있을 때만 채워진다. */}
          {result.opportunity_suggestions?.length > 0 && (
            <div className="user-view-opportunities">
              <h3 className="opportunities-title">
                <SparkIcon />
                이런 기회가 있습니다
              </h3>
              <ul className="opportunities-list">
                {result.opportunity_suggestions.map((text, i) => (
                  <li key={i}>{text}</li>
                ))}
              </ul>
            </div>
          )}

          {showFinalSynthesis && (
            <div className={`final-synthesis final-synthesis-${finalSynthesisStatus || 'streaming'}`}>
              <h3 className="final-synthesis-title">최종 분석</h3>
              <div className="final-synthesis-body">
                <FinalSynthesisText text={finalSynthesisText} streaming={finalSynthesisStatus === 'streaming'} />
              </div>
            </div>
          )}

          {(() => {
            const scholar = scholarItems(result)
            const adoption = adoptionItems(result)
            if (scholar.length === 0 && adoption.length === 0) return null
            return (
              <>
                {/* 여기까지는 우리 판정/해석이고, 여기부터는 그 판정에 쓴 원문 근거(링크) —
                    "해석"과 "원자료"를 구분선으로 나눠서 성격이 다르다는 걸 보여준다. */}
                <div className="section-divider" />
                <div className="user-view-evidence">
                {scholar.length > 0 && (
                  <EvidenceGroup
                    title="찾은 학술 근거"
                    items={scholar}
                    showCitation
                    page={evidencePage.scholar}
                    onPageChange={(p) => setEvidencePage((v) => ({ ...v, scholar: p }))}
                  />
                )}
                {adoption.length > 0 && (
                  <EvidenceGroup
                    title="찾은 산업 검색 결과"
                    items={adoption}
                    page={evidencePage.adoption}
                    onPageChange={(p) => setEvidencePage((v) => ({ ...v, adoption: p }))}
                  />
                )}
                </div>
              </>
            )
          })()}

          {/* 계산 근거 — 일반 사용자는 안 봐도 되는 검증용 디테일(점수 계산식, 링크 단위
              유사도 %)이라 기본은 접어두고, 필요한 사람만 펼쳐 보게 한다. */}
          {(result.gap_candidate?.score_breakdown ||
            (result.gap_candidate?.links || result.links)?.length > 0) && (
            <details className="score-breakdown">
              <summary className="score-breakdown-toggle">
                <span className="score-breakdown-label">계산 근거 자세히 보기</span>
              </summary>
              {result.gap_candidate?.score_breakdown && (
                <div className="score-breakdown-list">
                  {Object.entries(result.gap_candidate.score_breakdown).map(([name, breakdown]) => (
                    <span key={name} className="score-breakdown-item">
                      {name.replaceAll('_', ' ')} {breakdown.total}/100
                    </span>
                  ))}
                </div>
              )}
              <StructuredLinks links={result.gap_candidate?.links || result.links} />
            </details>
          )}
        </div>
      )}
    </div>
  )
}
