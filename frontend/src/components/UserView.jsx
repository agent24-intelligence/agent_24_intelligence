import { useEffect, useRef, useState } from 'react'
import { connectStream } from '../lib/sse'

// 사용자가 실제로 보는 화면. 주제 입력 → 진행 상황(친화적 문구) → 결과(Gap Map).
// Raw API Stream/API 테스트와 분리된, 이 프로젝트의 유일한 "제품" 화면.

const STAGE_LABEL = {
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
const LABEL_TEXT = {
  gap_candidate: '아직 현장엔 없어요',
  weak_gap_candidate: '조금 뒤처져 있어요',
  insufficient_evidence: '아직 판단하기 일러요',
  unconfirmed_field: '분야를 다시 확인해주세요',
  no_gap: '이미 잘 쓰이고 있어요',
  over_adopted: '연구보다 앞서가고 있어요',
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

function StopIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
      <rect x="5" y="5" width="14" height="14" rx="2" />
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
  const disconnectRef = useRef(null)
  const abortRef = useRef(null)

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

  async function runAnalyze(e) {
    e.preventDefault()
    if (!topic.trim()) return
    setStatus('running')
    setResult(null)
    setArtifact(null)
    setErrorMsg(null)
    setStage(null)

    const controller = new AbortController()
    abortRef.current = controller

    try {
      const res = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ topic, max_results: 10 }),
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
            ? '서버 응답을 이해하지 못했어요. 잠시 후 다시 시도해주세요.'
            : `서버에 문제가 생겼어요 (${res.status}). 잠시 후 다시 시도해주세요.`,
        )
      }

      if (!res.ok) {
        const detail = Array.isArray(data?.detail)
          ? data.detail.map((d) => d.msg || JSON.stringify(d)).join(', ')
          : data?.detail
        throw new Error(detail || `요청이 실패했어요 (${res.status}). 잠시 후 다시 시도해주세요.`)
      }

      setResult(data)
      setStatus('done')
    } catch (err) {
      if (err.name === 'AbortError') {
        setStatus('idle')
        setStage(null)
        return
      }
      setErrorMsg(err instanceof Error ? err.message : '알 수 없는 오류가 발생했어요. 잠시 후 다시 시도해주세요.')
      setStatus('error')
    } finally {
      abortRef.current = null
    }
  }

  function stopAnalyze() {
    abortRef.current?.abort()
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
        <p>관심 있는 기술이나 연구 방법론을 입력하면, 학계 연구는 앞서 있지만 아직 산업 현장에는 도입되지 않은 지점을 찾아드려요.</p>
      </div>

      <form className="user-view-form" onSubmit={runAnalyze}>
        <input
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder={`예: ${topicExample}`}
          disabled={status === 'running'}
        />
        {status === 'running' ? (
          <button type="button" className="user-view-icon-btn is-stop" onClick={stopAnalyze} aria-label="분석 중단">
            <StopIcon />
          </button>
        ) : (
          <button type="submit" className="user-view-icon-btn" disabled={!topic.trim()} aria-label="분석 시작">
            <ArrowIcon />
          </button>
        )}
      </form>

      {status === 'running' && (
        <div className="user-view-progress">
          <span className="spinner" />
          {stage ? STAGE_LABEL[stage] || stage : '준비하는 중'}
        </div>
      )}

      {status === 'error' && <div className="user-view-error">{errorMsg}</div>}

      {status === 'done' && result && (
        <div className="user-view-result">
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

          <div className={`user-view-label ${LABEL_TONE[result.label] || 'label-neutral'}`}>
            {LABEL_TEXT[result.label] || result.label}
          </div>
          {result.rationale && <p className="user-view-rationale">{result.rationale}</p>}

          {artifact?.html && (
            <>
              <div className="gap-map-caption">OpenAI 판정 결과를 Liner Visualization으로 최종 표현</div>
              <iframe title="gap-map" className="gap-map-frame" srcDoc={artifact.html} sandbox="allow-scripts" />
            </>
          )}
        </div>
      )}
    </div>
  )
}
