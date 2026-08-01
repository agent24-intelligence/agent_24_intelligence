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
  gap_candidate_generator: '갭 여부 판정하는 중',
  adversarial_verifier: '반증 검토하는 중',
  conditional_deep_research: '심층 조사하는 중',
  gap_map: '결과 정리하는 중',
}

// 사용자에게 그대로 보여주는 판정 문구 — 학술 라벨 대신 결과를 바로 이해할 수 있는 말투로.
// 소개 문구("~분석합니다")와 톤을 맞춰서 정중체(합니다체)로 통일.
const LABEL_TEXT = {
  gap_candidate: '아직 산업 현장에 적용되지 않았습니다',
  weak_gap_candidate: '산업 적용이 다소 늦은 편입니다',
  insufficient_evidence: '판단할 근거가 아직 부족합니다',
  unconfirmed_field: '입력한 분야를 다시 확인해주세요',
  no_gap: '이미 산업에 도입되어 있습니다',
  over_adopted: '연구보다 산업 적용이 앞서 있습니다',
}

// 결과 라벨을 의미에 맞는 톤으로 구분한다 (앰버 = 갭 시그널, 그린 = 해소/정리됨,
// 레드 = 반대 방향 경고, 슬레이트 = 판단 보류) — 사용자가 한눈에 결과 성격을 읽도록.
const LABEL_TONE = {
  gap_candidate: 'label-gap',
  weak_gap_candidate: 'label-weak',
  insufficient_evidence: 'label-neutral',
  unconfirmed_field: 'label-neutral',
  no_gap: 'label-resolved',
  over_adopted: 'label-alert',
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

// 정지(■) 대신 취소를 뜻하는 X — 미디어 플레이어 맥락이 없으면 정지 아이콘은
// 잘 안 읽힌다는 피드백이 있어서 바꿈.
function StopIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
      <line x1="6" y1="6" x2="18" y2="18" />
      <line x1="18" y1="6" x2="6" y2="18" />
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
  const [status, setStatus] = useState('idle') // idle | running | done | error
  const [stage, setStage] = useState(null)
  const [result, setResult] = useState(null)
  const [artifact, setArtifact] = useState(null)
  const [errorMsg, setErrorMsg] = useState(null)
  const [liveSuggestions, setLiveSuggestions] = useState([])
  const disconnectRef = useRef(null)
  const abortRef = useRef(null)
  const suggestAbortRef = useRef(null)

  useEffect(() => {
    disconnectRef.current = connectStream((event) => {
      if (event.stage) setStage(event.stage)
      if (event.type === 'data-atlas') {
        const atlas = event.payload?.data?.atlasArtifact ?? event.payload?.atlasArtifact
        if (atlas?.html) setArtifact(atlas)
      }
    })
    return () => disconnectRef.current?.()
  }, [])

  // 제출 전, 타이핑하는 동안 가볍게 /api/suggestions로 추천 검색어를 미리 보여준다.
  // 입력을 멈추고 잠깐(500ms) 있어야 호출해서, 한 글자씩 칠 때마다 요청이 나가진 않는다.
  useEffect(() => {
    if (status !== 'idle' || topic.trim().length < 2) {
      setLiveSuggestions([])
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
        setLiveSuggestions(Array.isArray(data?.recommendations) ? data.recommendations : [])
      } catch {
        // 보조 기능이라 실패해도 조용히 무시한다 — 사용자가 그냥 계속 타이핑하면 됨.
      }
    }, 500)

    return () => clearTimeout(timer)
  }, [topic, status])

  async function runAnalyze(e, topicOverride) {
    e?.preventDefault()
    const topicToRun = topicOverride ?? topic
    if (!topicToRun.trim()) return
    if (topicOverride) setTopic(topicOverride)
    setStatus('running')
    setResult(null)
    setArtifact(null)
    setErrorMsg(null)
    setStage(null)
    setLiveSuggestions([])

    const controller = new AbortController()
    abortRef.current = controller

    try {
      const res = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ topic: topicToRun, max_results: 10 }),
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
  }

  const showScrollTop = useScrollPastTop()

  return (
    <div className="user-view">
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
        <span className="user-view-kicker">
          <span className="radar-icon" />
          Gap Radar
        </span>
        <h1>Research-to-Reality Radar</h1>
        <p>연구 분야/기술 하나를 입력하면, 학계 연구와 산업 적용 사이의 격차를 분석합니다.</p>
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
            disabled={!topic.trim()}
            aria-label="분석 시작"
            title="분석 시작"
          >
            <ArrowIcon />
          </button>
        )}
      </form>

      {status === 'idle' && liveSuggestions.length > 0 && (
        <div className="user-view-live-suggestions">
          <span className="live-suggestions-label">이런 주제는 어떠세요?</span>
          <div className="user-view-suggestions">
            {liveSuggestions.map((rec) => (
              <button key={rec} type="button" className="suggestion-chip" onClick={() => applySuggestion(rec)}>
                {rec}
              </button>
            ))}
          </div>
        </div>
      )}

      {status === 'running' && (
        <div className="user-view-progress">
          <span className="spinner" />
          {stage ? STAGE_LABEL[stage] || stage : '준비하는 중'}
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
            {/* 사전 검사 모델이 추천을 만들어놓고도 rejected 전용 문구("추천 검색어가
                없어요...")를 잘못 재사용하는 경우가 있어서, 추천이 실제로 있으면
                그 모순된 문구 대신 자연스러운 안내로 바꿔 보여준다. */}
            {result.recommendations?.length > 0 && result.message?.includes('추천 검색어가 없어요')
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
          {result.preflight?.status === 'auto_corrected' && (
            // 검색엔진의 "이 검색어에 대한 결과가 없어 다음으로 표시합니다" 안내와 같은
            // 익숙한 패턴 — 카드/배지보다 이게 훨씬 신뢰가 가는 관용구라 그대로 따름.
            <p className="user-view-corrected-note">
              <span>‘{result.input_topic}’에 대한 검색결과가 없어 다음에 대한 결과를 표시합니다:</span>{' '}
              <strong className="corrected-note-term">{result.topic}</strong>
            </p>
          )}

          <div className={`user-view-label ${LABEL_TONE[result.label] || 'label-neutral'}`}>
            {LABEL_TEXT[result.label] || result.label}
          </div>

          <div className="user-view-scores">
            <div className="score-card">
              <span className="score-label">연구는 얼마나 진행됐나</span>
              <span className="score-value">{result.scores?.evidence_maturity ?? '-'}</span>
            </div>
            <div className="score-card">
              <span className="score-label">실제로 쓰이고 있나</span>
              <span className="score-value">{result.scores?.adoption_evidence ?? '-'}</span>
            </div>
            <div className="score-card">
              <span className="score-label">얼마나 꼼꼼히 찾아봤나</span>
              <span className="score-value">{result.scores?.coverage_confidence ?? '-'}</span>
            </div>
          </div>

          {result.rationale && <p className="user-view-rationale">{result.rationale}</p>}

          {artifact?.html && (
            <>
              <div className="gap-map-caption">OpenAI 판정 결과를 Liner Visualization으로 최종 표현</div>
              {/* 안쪽에 별도 스크롤바가 생기지 않도록, 로드되면 내용 높이만큼 iframe 자체 높이를 늘려서
                  스크롤이 페이지 하나로만 일어나게 한다. */}
              <iframe
                title="gap-map"
                className="gap-map-frame"
                srcDoc={artifact.html}
                sandbox="allow-scripts allow-same-origin"
                onLoad={(e) => {
                  const iframe = e.currentTarget
                  const doc = iframe.contentDocument
                  if (!doc?.documentElement) return

                  // Liner 위젯이 html/body가 아니라 자기 안의 어떤 wrapper div에
                  // height:100vh + overflow:auto를 직접 걸어서 스스로 스크롤 영역을 만드는
                  // 경우가 있다. html/body만 풀어서는 그 wrapper까지는 안 풀리니, 실제로
                  // overflow가 걸린 요소를 전부 찾아서 같이 풀어준다.
                  const unclamp = () => {
                    doc.documentElement.style.setProperty('height', 'auto', 'important')
                    doc.documentElement.style.setProperty('min-height', '0', 'important')
                    doc.documentElement.style.setProperty('overflow', 'visible', 'important')
                    if (doc.body) {
                      doc.body.style.setProperty('height', 'auto', 'important')
                      doc.body.style.setProperty('min-height', '0', 'important')
                      doc.body.style.setProperty('overflow', 'visible', 'important')
                    }
                    doc.querySelectorAll('*').forEach((el) => {
                      const cs = doc.defaultView?.getComputedStyle(el)
                      if (!cs) return
                      const hasScroll = [cs.overflow, cs.overflowX, cs.overflowY].some((v) => ['auto', 'scroll'].includes(v))
                      const isLinerWrapper = el.id === 'vis-container'
                      if (hasScroll || isLinerWrapper) {
                        el.style.setProperty('overflow', 'visible', 'important')
                        el.style.setProperty('overflow-x', 'visible', 'important')
                        el.style.setProperty('overflow-y', 'visible', 'important')
                        el.style.setProperty('height', 'auto', 'important')
                        el.style.setProperty('min-height', '0', 'important')
                        el.style.setProperty('max-height', 'none', 'important')
                      }
                    })
                  }

                  const measureHeight = () => {
                    const rootTop = doc.documentElement.getBoundingClientRect().top
                    let visualBottom = 0
                    doc.querySelectorAll('body *').forEach((el) => {
                      const rect = el.getBoundingClientRect()
                      if (rect.width > 0 || rect.height > 0) {
                        visualBottom = Math.max(visualBottom, rect.bottom - rootTop)
                      }
                    })

                    return Math.ceil(Math.max(
                      doc.documentElement.scrollHeight,
                      doc.body?.scrollHeight || 0,
                      visualBottom,
                      400,
                    ))
                  }

                  const resize = () => {
                    unclamp()
                    iframe.style.height = `${measureHeight()}px`
                  }
                  resize()

                  // 차트 라이브러리가 로드 직후 비동기로 그려지는 경우 크기가 처음엔 작게
                  // 잡혀서 한 번으로는 부족하다. 계속 감시해서 늘어나면 다시 맞춘다.
                  if ('ResizeObserver' in window && doc.body) {
                    const ro = new ResizeObserver(resize)
                    ro.observe(doc.body)
                    const visContainer = doc.getElementById('vis-container')
                    if (visContainer) ro.observe(visContainer)
                  } else {
                    resize()
                  }
                  ;[200, 600, 1200, 2200].forEach((delay) => setTimeout(resize, delay))
                }}
              />
            </>
          )}
        </div>
      )}
    </div>
  )
}
