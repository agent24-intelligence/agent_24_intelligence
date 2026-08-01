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
  gap_map: '결과 정리하는 중',
}

// 사용자에게 그대로 보여주는 판정 문구 — 학술 라벨 대신 결과를 바로 이해할 수 있는 말투로.
// 소개 문구("~분석합니다")와 톤을 맞춰서 정중체(합니다체)로 통일.
const LABEL_TEXT = {
  gap_candidate: '아직 산업 현장에 적용되지 않았습니다',
  emerging_adoption: '산업 적용이 초기 단계입니다',
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

// 점수(0~100)를 명암 강도 클래스로 매핑 — 무채색 베이스 안에서 값이 클수록 진하게.
function scoreTone(value) {
  if (typeof value !== 'number') return ''
  if (value >= 67) return 'score-high'
  if (value >= 34) return 'score-mid'
  return 'score-low'
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

export default function UserView() {
  const [topicExample] = useState(() => TOPIC_EXAMPLES[Math.floor(Math.random() * TOPIC_EXAMPLES.length)])
  const [topic, setTopic] = useState('')
  const [fastMode, setFastMode] = useState(false)
  const [status, setStatus] = useState('idle') // idle | running | done | error
  const [stage, setStage] = useState(null)
  const [result, setResult] = useState(null)
  const [errorMsg, setErrorMsg] = useState(null)
  const [liveSuggestions, setLiveSuggestions] = useState([])
  const [liveGuidance, setLiveGuidance] = useState(null) // 추천이 하나도 없을 때(rejected) 보여줄 안내 문구
  const [evidencePage, setEvidencePage] = useState({ scholar: 0, adoption: 0 })
  const [searchNote, setSearchNote] = useState(null) // { mode, query } — 지금 이 순간 실제로 던지고 있는 검색어
  const [lastAnalyzedTopic, setLastAnalyzedTopic] = useState(null) // 직전에 실제로 돌린 주제 — 그대로 재요청하는 걸 막는 데 씀
  const disconnectRef = useRef(null)
  const abortRef = useRef(null)
  const suggestAbortRef = useRef(null)

  useEffect(() => {
    disconnectRef.current = connectStream((event) => {
      if (event.stage) setStage(event.stage)
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
    if (status === 'running' || topic.trim().length < 2) {
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
  }, [topic, status])

  async function runAnalyze(e, topicOverride) {
    e?.preventDefault()
    const topicToRun = topicOverride ?? topic
    if (!topicToRun.trim()) return
    // 결과가 이미 떠 있는 상태에서 입력을 안 고치고 그대로 화살표를 또 누르면, 방금과
    // 똑같은 요청을 API 비용 들여가며 한 번 더 돌리게 된다. 직전에 성공한 주제와
    // 완전히 같으면 그냥 지금 보이는 결과를 유지하고 재요청은 건너뛴다.
    if (status === 'done' && topicToRun.trim() === lastAnalyzedTopic) return
    if (topicOverride) setTopic(topicOverride)
    setStatus('running')
    setResult(null)
    setErrorMsg(null)
    setStage(null)
    setSearchNote(null)
    setLiveSuggestions([])
    setLiveGuidance(null)
    setEvidencePage({ scholar: 0, adoption: 0 })

    const controller = new AbortController()
    abortRef.current = controller

    try {
      const res = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ topic: topicToRun, max_results: 10, fast_mode: fastMode }),
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

      setResult(data)
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
    runAnalyze(null, rec)
  }

  // 타이핑 중 뜬 실시간 추천은 아직 제출 전이라, 클릭하면 입력창만 채우고
  // 사용자가 직접 확인 후 제출하도록 둔다 (바로 분석을 돌리지 않음).
  function applySuggestion(rec) {
    setTopic(rec)
    setLiveSuggestions([])
    setLiveGuidance(null)
  }

  const showScrollTop = useScrollPastTop()

  return (
    <div className="user-view">
      <button
        type="button"
        className={`fast-mode-toggle ${fastMode ? 'is-on' : ''}`}
        onClick={() => {
          setFastMode((value) => !value)
          setLastAnalyzedTopic(null)
        }}
        aria-pressed={fastMode}
        disabled={status === 'running'}
        title={fastMode ? '단계별 시간 제한을 적용합니다' : '시간 제한 없이 전체 분석을 실행합니다'}
      >
        <span className="fast-mode-dot" />
        <span>Fast mode</span>
        <span className="fast-mode-state">{fastMode ? 'ON' : 'OFF'}</span>
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
          연구는 많지만, 산업으로 이어지는 기술은 많지 않습니다.
          <br />
          연구 주제나 산업 아이디어를 입력하면, AI가 그 사이의 격차를 분석하고 근거를 보여줍니다.
        </p>
      </div>

      <form className="user-view-form" onSubmit={runAnalyze}>
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

      {status !== 'running' && liveSuggestions.length > 0 && (
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
      {status !== 'running' && liveSuggestions.length === 0 && liveGuidance && (
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
      {status === 'done' && result && result.status !== 'completed' && (
        <div className="user-view-guidance">
          <p className="user-view-guidance-message">
            {/* 사전 검사 모델이 추천을 만들어놓고도 rejected 전용 문구("검색어를 다시
                확인해 주세요")를 잘못 재사용하는 경우가 있어서, 추천이 실제로 있으면
                그 모순된 문구 대신 자연스러운 안내로 바꿔 보여준다. */}
            {result.recommendations?.length > 0 && result.message?.includes('검색어를 다시 확인해')
              ? '입력하신 주제로 아래 추천 검색어를 만들었어요. 하나를 선택해 보세요.'
              : result.message}
          </p>
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

          <div className="user-view-scores">
            <div className={`score-card ${scoreTone(result.scores?.evidence_maturity)}`}>
              <span className="score-label">연구는 얼마나 진행됐나</span>
              <span className={`score-value ${scoreTone(result.scores?.evidence_maturity)}`}>
                {typeof result.scores?.evidence_maturity === 'number' ? `${result.scores.evidence_maturity}%` : '-'}
              </span>
            </div>
            <div className={`score-card ${scoreTone(result.scores?.adoption_evidence)}`}>
              <span className="score-label">실제로 쓰이고 있나</span>
              <span className={`score-value ${scoreTone(result.scores?.adoption_evidence)}`}>
                {typeof result.scores?.adoption_evidence === 'number' ? `${result.scores.adoption_evidence}%` : '-'}
              </span>
            </div>
            <div className={`score-card ${scoreTone(result.scores?.coverage_confidence)}`}>
              <span className="score-label">판정 신뢰도</span>
              <span className={`score-value ${scoreTone(result.scores?.coverage_confidence)}`}>
                {typeof result.scores?.coverage_confidence === 'number' ? `${result.scores.coverage_confidence}%` : '-'}
              </span>
            </div>
          </div>

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

          {result.gap_candidate?.score_breakdown && (
            <div className="score-breakdown">
              <span className="score-breakdown-label">계산 근거</span>
              <div className="score-breakdown-list">
                {Object.entries(result.gap_candidate.score_breakdown).map(([name, breakdown]) => (
                  <span key={name} className="score-breakdown-item">
                    {name.replaceAll('_', ' ')} {breakdown.total}/100
                  </span>
                ))}
              </div>
            </div>
          )}

          {result.rationale && <p className="user-view-rationale">{result.rationale}</p>}

          {/* 판정 설명(라벨/점수/rationale)과 그 아래 보조 설명 묶음을 구분선으로 나눈다 —
              전부 텍스트/카드가 이어 붙어있으면 어디까지가 "판정"이고 어디부터가
              "부가 설명"인지 눈으로 구분이 잘 안 됐다. */}
          {(result.vocabulary?.rationale ||
            result.connected_points?.length > 0 ||
            result.gap_points?.length > 0 ||
            result.potential_points?.length > 0 ||
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

          {(result.connected_points?.length > 0 ||
            result.gap_points?.length > 0 ||
            result.potential_points?.length > 0) && (
            <div className="user-view-connections">
              {result.connected_points?.length > 0 && (
                <ConnectionGroup tone="connected" title="연계된 근거" items={result.connected_points} />
              )}
              {result.gap_points?.length > 0 && (
                <ConnectionGroup tone="gap" title="연계 안 된 부분 — 진짜 갭" items={result.gap_points} />
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

          <StructuredLinks links={result.gap_candidate?.links || result.links} />

          {(result.confirmed_barriers?.length > 0 || result.inferred_barriers?.length > 0) && (
            <div className="user-view-connections">
              {result.confirmed_barriers?.length > 0 && (
                <ConnectionGroup tone="gap" title="확인된 장벽" items={result.confirmed_barriers} />
              )}
              {result.inferred_barriers?.length > 0 && (
                <ConnectionGroup tone="potential" title="추론된 장벽" items={result.inferred_barriers} />
              )}
            </div>
          )}

          {/* 관찰(potential_points)에서 한 단계 더 나가서 "그럼 뭘 만들면 되는지" 실행
              가능한 제안까지 준다 — 결과 화면에서 가장 실질적인 정보라 포인트 컬러로
              강조해서 다른 회색 카드들 사이에서 눈에 띄게 한다. */}
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
                    title="찾은 산업 도입 근거"
                    items={adoption}
                    page={evidencePage.adoption}
                    onPageChange={(p) => setEvidencePage((v) => ({ ...v, adoption: p }))}
                  />
                )}
                </div>
              </>
            )
          })()}

        </div>
      )}
    </div>
  )
}
