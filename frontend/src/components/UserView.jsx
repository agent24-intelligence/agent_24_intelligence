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

const LABEL_TEXT = {
  gap_candidate: '적용 갭 후보',
  weak_gap_candidate: '약한 갭 후보',
  insufficient_evidence: '근거 부족',
  unconfirmed_field: '분야 확인 안 됨',
  no_gap: '갭 없음',
  over_adopted: '과잉 적용',
}

export default function UserView() {
  const [topic, setTopic] = useState('')
  const [status, setStatus] = useState('idle') // idle | running | done | error
  const [stage, setStage] = useState(null)
  const [result, setResult] = useState(null)
  const [artifact, setArtifact] = useState(null)
  const [errorMsg, setErrorMsg] = useState(null)
  const disconnectRef = useRef(null)

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

    try {
      const res = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ topic, max_results: 10 }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data?.detail ? JSON.stringify(data.detail) : `요청 실패 (${res.status})`)
      setResult(data)
      setStatus('done')
    } catch (err) {
      setErrorMsg(String(err))
      setStatus('error')
    }
  }

  return (
    <div className="user-view">
      <div className="user-view-intro">
        <h1>Research-to-Reality Radar</h1>
        <p>주제 하나를 입력하면 학계-산업 간 적용 갭을 근거와 함께 정리합니다.</p>
      </div>

      <form className="user-view-form" onSubmit={runAnalyze}>
        <input
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder="예: 온디바이스 sLLM 양자화"
          disabled={status === 'running'}
        />
        <button type="submit" disabled={status === 'running' || !topic.trim()}>
          {status === 'running' ? '분석 중...' : '분석 시작'}
        </button>
      </form>

      {status === 'running' && (
        <div className="user-view-progress">
          <span className="spinner" />
          {stage ? STAGE_LABEL[stage] || stage : '준비하는 중'}
        </div>
      )}

      {status === 'error' && <div className="user-view-error">분석에 실패했습니다: {errorMsg}</div>}

      {status === 'done' && result && (
        <div className="user-view-result">
          <div className="user-view-scores">
            <div className="score-card">
              <span className="score-label">연구 근거 성숙도</span>
              <span className="score-value">{result.scores?.evidence_maturity ?? '-'}</span>
            </div>
            <div className="score-card">
              <span className="score-label">공개 도입 증거</span>
              <span className="score-value">{result.scores?.adoption_evidence ?? '-'}</span>
            </div>
            <div className="score-card">
              <span className="score-label">검색 커버리지</span>
              <span className="score-value">{result.scores?.coverage_confidence ?? '-'}</span>
            </div>
          </div>

          <div className="user-view-label">{LABEL_TEXT[result.label] || result.label}</div>
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
