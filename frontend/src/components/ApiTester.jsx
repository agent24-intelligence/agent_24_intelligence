import { useState } from 'react'

const DEFAULT_BODY = '{\n  \n}'

async function readJsonOrText(res) {
  const text = await res.text()
  if (!text.trim()) {
    return { error: 'empty_response', status: res.status }
  }
  try {
    return JSON.parse(text)
  } catch (err) {
    return { error: `응답 JSON 파싱 실패: ${err.message}`, status: res.status, body: text }
  }
}

export default function ApiTester() {
  const [analyzeTopic, setAnalyzeTopic] = useState('온디바이스 sLLM 양자화')
  const [analyzeStatus, setAnalyzeStatus] = useState('')
  const [analyzeResult, setAnalyzeResult] = useState(null)
  const [target, setTarget] = useState('openai')
  const [method, setMethod] = useState('POST')
  const [path, setPath] = useState('/chat/completions')
  const [bodyText, setBodyText] = useState(DEFAULT_BODY)
  const [result, setResult] = useState(null)
  const [sending, setSending] = useState(false)
  const [demoStatus, setDemoStatus] = useState('')

  async function sendTest(e) {
    e.preventDefault()
    setSending(true)
    setResult(null)
    let json
    try {
      json = bodyText.trim() ? JSON.parse(bodyText) : undefined
    } catch (err) {
      setResult({ error: `요청 본문 JSON 파싱 실패: ${err.message}` })
      setSending(false)
      return
    }
    try {
      const res = await fetch(`/api/proxy/${target}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ method, path, json }),
      })
      const data = await readJsonOrText(res)
      setResult(data)
    } catch (err) {
      setResult({ error: String(err) })
    } finally {
      setSending(false)
    }
  }

  async function runDemo() {
    setDemoStatus('실행 중...')
    try {
      await fetch('/api/demo/run', { method: 'POST' })
      setDemoStatus('시작됨 — Raw API Stream 패널에서 확인')
    } catch (err) {
      setDemoStatus(`실패: ${err}`)
    }
  }

  async function runAnalyze(e) {
    e.preventDefault()
    setAnalyzeStatus('실행 중...')
    setAnalyzeResult(null)

    try {
      const res = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ topic: analyzeTopic, max_results: 10 }),
      })
      const data = await readJsonOrText(res)
      setAnalyzeResult(data)
      setAnalyzeStatus(res.ok ? '완료됨 — Raw API Stream 패널에서 단계별 이벤트 확인' : `실패 (${res.status})`)
    } catch (err) {
      setAnalyzeStatus(`실패: ${err}`)
    }
  }

  return (
    <div className="api-tester">
      <div className="api-tester-section">
        <h3>실전 파이프라인 실행</h3>
        <p className="hint">입력한 주제로 Scholar/Web 검색 파이프라인을 시작한다.</p>
        <form onSubmit={runAnalyze}>
          <div className="form-row">
            <input
              value={analyzeTopic}
              onChange={(e) => setAnalyzeTopic(e.target.value)}
              placeholder="분석할 주제"
              required
            />
            <button type="submit" disabled={!analyzeTopic.trim() || analyzeStatus === '실행 중...'}>
              실전 파이프라인 실행
            </button>
          </div>
        </form>
        {analyzeStatus && <div className="demo-status">{analyzeStatus}</div>}
        {analyzeResult && <pre className="result-box">{JSON.stringify(analyzeResult, null, 2)}</pre>}
      </div>

      <div className="api-tester-section">
        <h3>모의 파이프라인 실행</h3>
        <p className="hint">실제 백엔드 파이프라인이 붙기 전, 이벤트 흐름 확인용 스텁을 돌린다.</p>
        <button onClick={runDemo}>모의 파이프라인 실행</button>
        {demoStatus && <div className="demo-status">{demoStatus}</div>}
      </div>

      <div className="api-tester-section">
        <h3>API 수동 테스트</h3>
        <p className="hint">키는 서버(.env)에서만 사용된다. 요청/응답은 Raw API Stream에도 같이 찍힌다.</p>
        <form onSubmit={sendTest}>
          <div className="form-row">
            <select value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="openai">openai</option>
              <option value="liner">liner</option>
            </select>
            <select value={method} onChange={(e) => setMethod(e.target.value)}>
              <option value="GET">GET</option>
              <option value="POST">POST</option>
            </select>
            <input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/chat/completions" />
          </div>
          <textarea value={bodyText} onChange={(e) => setBodyText(e.target.value)} rows={8} spellCheck={false} />
          <button type="submit" disabled={sending}>
            {sending ? '전송 중...' : '요청 보내기'}
          </button>
        </form>
        {result && <pre className="result-box">{JSON.stringify(result, null, 2)}</pre>}
      </div>
    </div>
  )
}
