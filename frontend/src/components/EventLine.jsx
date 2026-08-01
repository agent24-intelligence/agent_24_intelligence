// 이벤트 한 건을 그대로 보여준다. 알려진 타입은 톤 배지 색만 다르게 주고,
// 처음 보는 타입이 와도 fallback(중립 회색 배지 + JSON 원문)으로 항상 렌더링된다 — 절대 죽지 않는다.
// 무채색 기본 + tool_call만 포인트 컬러로 강조, error만 관례상 빨강 유지.
// 나머지 이벤트 타입은 전부 중립 회색으로 통일해 색이 산만해지지 않게 한다.
const TYPE_TONE = {
  tool_call: 'tone-accent',
  error: 'tone-error',
}

function toneClass(type) {
  return TYPE_TONE[type] || 'tone-neutral' // 미상 타입 포함, 그 외는 전부 중립 톤
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
