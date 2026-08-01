import { useEffect, useRef, useState } from 'react'
import { connectStream } from '../lib/sse'
import EventLine from './EventLine'

const MAX_LINES = 500 // 화면 하나가 무한정 늘어나지 않도록만 제한. 데이터 손실은 서버 히스토리(200개)가 커버.

export default function RawStream() {
  const [events, setEvents] = useState([])
  const [status, setStatus] = useState('connecting')
  const [follow, setFollow] = useState(true)
  const bottomRef = useRef(null)

  useEffect(() => {
    const disconnect = connectStream(
      (event) => {
        setEvents((prev) => {
          const next = [...prev, event]
          return next.length > MAX_LINES ? next.slice(next.length - MAX_LINES) : next
        })
      },
      setStatus,
    )
    return disconnect
  }, [])

  useEffect(() => {
    if (follow) bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [events, follow])

  return (
    <div className="raw-stream">
      <div className="raw-stream-toolbar">
        <span className={`status-dot status-${status}`} />
        <span className="status-label">{status}</span>
        <span className="event-count">{events.length}건</span>
        <label className="follow-toggle">
          <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
          자동 스크롤
        </label>
        <button onClick={() => setEvents([])}>화면 비우기</button>
      </div>
      <div className="raw-stream-body">
        {events.length === 0 && <div className="empty-hint">아직 이벤트 없음. 파이프라인 실행을 기다리는 중.</div>}
        {events.map((event) => (
          <EventLine key={event.id} event={event} />
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
