// 이벤트 한 건을 그대로 보여준다. 알려진 타입은 톤 배지 색만 다르게 주고,
// 처음 보는 타입이 와도 fallback(중립 회색 배지 + JSON 원문)으로 항상 렌더링된다 — 절대 죽지 않는다.
const TYPE_TONE = {
  tool_call: 'tone-blue',
  tool_result: 'tone-green',
  error: 'tone-red',
  note: 'tone-slate',
  finish: 'tone-green',
  'text-start': 'tone-teal',
  'text-delta': 'tone-teal',
  'text-end': 'tone-teal',
  'reasoning-delta': 'tone-amber',
  'reasoning-end': 'tone-amber',
  sse_line: 'tone-cyan',
}

function toneClass(type) {
  return TYPE_TONE[type] || 'tone-slate' // 미상 타입은 중립 톤으로 통일
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
        <span className={`event-type ${toneClass(type)}`}>{type}</span>
      </div>
      <pre className="event-payload">{typeof payload === 'string' ? payload : JSON.stringify(payload, null, 2)}</pre>
    </div>
  )
}
