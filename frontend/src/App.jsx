import { useState } from 'react'
import RawStream from './components/RawStream'
import ApiTester from './components/ApiTester'
import GapMap from './components/GapMap'
import UserView from './components/UserView'
import './App.css'

// 화면 세 종류를 URL로 분리한다.
// ?view=user (기본값)  — 실제 제품 화면. 주제 입력 + 결과만 보임. 탭/로그 없음.
// ?view=stream 또는 ?clean=1 — 결선 세컨드 화면용 Raw API Stream만.
// ?view=dev    — 팀 개발/디버깅용. 기존 탭 3개(Raw Stream/API 테스트/Gap Map).
const params = new URLSearchParams(window.location.search)
const view = params.get('clean') === '1' ? 'stream' : params.get('view') || 'user'

export default function App() {
  const [tab, setTab] = useState('stream')

  if (view === 'stream') {
    return (
      <div className="app app-clean">
        <RawStream />
      </div>
    )
  }

  if (view === 'user') {
    return (
      <div className="app">
        <main>
          <div className="content-max">
            <UserView />
          </div>
        </main>
        <a className="dev-link" href="?view=dev">
          dev
        </a>
      </div>
    )
  }

  return (
    <div className="app">
      <header className="app-header">
        <span className="app-title">
          <img src="/logo-filled.png" alt="" className="brand-mark" />
          AGENT:24 — dev
        </span>
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
        <a className="clean-link" href="?view=user">
          사용자 화면
        </a>
      </header>
      <main>
        <div className="content-max">
          {tab === 'stream' && <RawStream />}
          {tab === 'test' && <ApiTester />}
          {tab === 'gapmap' && <GapMap />}
        </div>
      </main>
    </div>
  )
}
