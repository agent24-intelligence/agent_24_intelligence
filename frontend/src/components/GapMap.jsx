import { useEffect, useState } from 'react'
import { connectStream } from '../lib/sse'

// Liner Visualization API 응답을 그대로 렌더링하는 탭.
// 백엔드가 파이프라인에서 emit_event("data-atlas", {...}) 등을 호출하면 (Raw Stream과 같은
// SSE 채널) 여기서 골라내서 atlasArtifact.html을 iframe에 그대로 띄운다.
// 참고: Liner 문서 기준 이벤트 순서 = start → start-step → (data-search-references) →
// data-atlas → finish-step → finish. 평균 지연 20~30초.

function fitIframeToContent(iframe) {
  const doc = iframe.contentDocument
  if (!doc?.documentElement) return

  const setUnclamped = (el) => {
    el.style.setProperty('height', 'auto', 'important')
    el.style.setProperty('min-height', '0', 'important')
    el.style.setProperty('max-height', 'none', 'important')
    el.style.setProperty('overflow', 'visible', 'important')
    el.style.setProperty('overflow-x', 'visible', 'important')
    el.style.setProperty('overflow-y', 'visible', 'important')
  }

  const unclamp = () => {
    setUnclamped(doc.documentElement)
    if (doc.body) setUnclamped(doc.body)

    doc.querySelectorAll('*').forEach((el) => {
      const cs = doc.defaultView?.getComputedStyle(el)
      if (!cs) return
      const hasScroll = [cs.overflow, cs.overflowX, cs.overflowY].some((v) => ['auto', 'scroll'].includes(v))
      if (hasScroll || el.id === 'vis-container') setUnclamped(el)
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

  if ('ResizeObserver' in window && doc.body) {
    const ro = new ResizeObserver(resize)
    ro.observe(doc.body)
    const visContainer = doc.getElementById('vis-container')
    if (visContainer) ro.observe(visContainer)
  }

  ;[200, 600, 1200, 2200].forEach((delay) => setTimeout(resize, delay))
}

export default function GapMap() {
  const [artifact, setArtifact] = useState(null) // { html, theme, description }
  const [references, setReferences] = useState([])
  const [error, setError] = useState(null)
  const [waiting, setWaiting] = useState(false)

  useEffect(() => {
    const disconnect = connectStream((event) => {
      const { type, payload } = event
      if (type === 'data-atlas') {
        const atlas = payload?.data?.atlasArtifact ?? payload?.atlasArtifact ?? payload
        if (atlas?.html) {
          setArtifact(atlas)
          setWaiting(false)
          setError(null)
        }
      } else if (type === 'data-search-references') {
        const refs = payload?.data?.references ?? payload?.references ?? []
        setReferences(refs)
        setWaiting(true)
      } else if (type === 'data-error') {
        setError(payload)
        setWaiting(false)
      } else if (type === 'tool_call' && payload?.name === 'visualization') {
        setWaiting(true)
        setArtifact(null)
        setError(null)
      }
    })
    return disconnect
  }, [])

  return (
    <div className="gap-map">
      <div className="gap-map-caption">OpenAI 판정 결과를 Liner Visualization으로 최종 표현</div>

      {!artifact && !error && (
        <div className="empty-hint">
          {waiting ? 'Gap Map 렌더링 대기 중 (평균 20~30초)...' : '아직 시각화 없음. 파이프라인이 visualization 단계에 도달하면 여기 뜬다.'}
        </div>
      )}

      {error && (
        <div className="gap-map-error">
          <div>Visualization 실패</div>
          <pre>{JSON.stringify(error, null, 2)}</pre>
        </div>
      )}

      {artifact && (
        <>
          <div className="gap-map-meta">
            {artifact.theme && <span className="event-stage">{artifact.theme}</span>}
            {artifact.description && <span className="gap-map-desc">{artifact.description}</span>}
          </div>
          <iframe
            title="gap-map"
            className="gap-map-frame"
            srcDoc={artifact.html}
            sandbox="allow-scripts allow-same-origin"
            onLoad={(e) => fitIframeToContent(e.currentTarget)}
          />
          {references.length > 0 && (
            <div className="gap-map-refs">
              <h4>참고 자료 ({references.length})</h4>
              <ul>
                {references.map((ref, i) => (
                  <li key={i}>
                    <a href={ref.url} target="_blank" rel="noreferrer">
                      {ref.title || ref.url}
                    </a>
                    {ref.hostname && <span> — {ref.hostname}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  )
}
