export function connectStream(onEvent, onStatus, url = '/api/stream') {
  const source = new EventSource(url)

  onStatus?.('connecting')

  source.onopen = () => onStatus?.('connected')
  source.onmessage = (message) => {
    if (!message.data) return

    try {
      onEvent(JSON.parse(message.data))
    } catch (error) {
      onStatus?.('error')
      console.error('Raw API Stream 이벤트 파싱 실패', error)
    }
  }
  source.onerror = () => onStatus?.('error')

  return () => source.close()
}
