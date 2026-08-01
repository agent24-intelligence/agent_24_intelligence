// 이벤트 한 건을 그대로 보여준다. 알려진 타입은 배지 색만 다르게 주고,
// 처음 보는 타입이 와도 아래 fallback(회색 배지 + JSON 원문)으로 항상 렌더링된다 — 절대 죽지 않는다.
const TYPE_STYLE = {
  tool_call: '#3b82f6',
  tool_result: '#22c55e',
  error: '#ef4444',
  note: '#a3a3a3',
  finish: '#22c55e',
  'text-start': '#8b5cf6',
  'text-delta': '#8b5cf6',
  'text-end': '#8b5cf6',
  'reasoning-delta': '#f59e0b',
  'reasoning-end': '#f59e0b',
  sse_line: '#06b6d4',
}

function badgeColor(type) {
  return TYPE_STYLE[type] || '#6b7280' // 미상 타입은 회색으로 통일
}

function formatTime(ts) {
  try {
    return new Date(ts).toLocaleTimeString('ko-KR', { hour12: false })
  } catch {
    return ts
  }
}

export default function EventLine({ event }) {
  const { ts, stage, source, type, payload } = event
  return (
    <div className="event-line">
      <div className="event-line-head">
        <span className="event-time">{formatTime(ts)}</span>
        {stage && <span className="event-stage">{stage}</span>}
        {source && <span className="event-source">{source}</span>}
        <span className="event-type" style={{ background: badgeColor(type) }}>
          {type}
        </span>
      </div>
      <pre className="event-payload">{typeof payload === 'string' ? payload : JSON.stringify(payload, null, 2)}</pre>
    </div>
  )
}
