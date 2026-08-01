import { useState } from 'react'
import RawStream from './components/RawStream'
import ApiTester from './components/ApiTester'
import GapMap from './components/GapMap'
import './App.css'

// 데모 세컨드 화면에서는 ?clean=1 로 열면 탭/테스트 패널 없이 스트림만 나온다.
const isCleanMode = new URLSearchParams(window.location.search).get('clean') === '1'

export default function App() {
  const [tab, setTab] = useState('stream')

  if (isCleanMode) {
    return (
      <div className="app app-clean">
        <RawStream />
      </div>
    )
  }

  return (
    <div className="app">
      <header className="app-header">
        <span className="app-title">AGENT:24 — Raw API Stream</span>
        <nav className="tabs">
          <button className={tab === 'stream' ? 'active' : ''} onClick={() => setTab('stream')}>
            Raw API Stream
          </button>
          <button className={tab === 'test' ? 'active' : ''} onClick={() => setTab('test')}>
            API 테스트
          </button>
          <button className={tab === 'gapmap' ? 'active' : ''} onClick={() => setTab('gapmap')}>
            Gap Map
          </button>
        </nav>
        <a className="clean-link" href="?clean=1" target="_blank" rel="noreferrer">
          세컨드 화면용 링크 (탭 없이)
        </a>
      </header>
      <main>
        {tab === 'stream' && <RawStream />}
        {tab === 'test' && <ApiTester />}
        {tab === 'gapmap' && <GapMap />}
      </main>
    </div>
  )
}
