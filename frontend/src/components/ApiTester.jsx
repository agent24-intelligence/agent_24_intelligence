import { useState } from 'react'

const DEFAULT_BODY = '{\n  \n}'

export default function ApiTester() {
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
      const data = await res.json()
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

  return (
    <div className="api-tester">
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
