import React from 'react'
import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import { useWebSocket } from './hooks/useWebSocket'
import Dashboard from './pages/Dashboard'
import Analytics from './pages/Analytics'
import MentorSchool from './pages/MentorSchool'
import NewsRoom from './pages/NewsRoom'
import Lab from './pages/Lab'
import AIChatBot from './components/AIChatBot'
import {
  LayoutDashboard, TrendingUp, BookOpen, Newspaper, Activity, FlaskConical
} from 'lucide-react'

function App() {
  const { connected, lastEvent } = useWebSocket('ws://localhost:8000/ws/live')

  return (
    <BrowserRouter>
      <div className="flex h-screen overflow-hidden bg-surface">
        {/* Sidebar */}
        <aside className="w-14 lg:w-52 flex flex-col bg-surface-1 border-r border-surface-3 shrink-0">
          {/* Logo */}
          <div className="flex items-center gap-2.5 px-3 lg:px-4 py-4 border-b border-surface-3">
            <div className="w-7 h-7 rounded-lg bg-brand flex items-center justify-center shrink-0">
              <Activity className="w-3.5 h-3.5 text-white" />
            </div>
            <div className="hidden lg:block">
              <div className="font-bold text-white text-sm leading-tight">TradeSage</div>
              <div className="text-[10px] text-gray-500 uppercase tracking-widest">AI Trading</div>
            </div>
          </div>

          {/* Nav */}
          <nav className="flex-1 px-2 py-3 space-y-0.5">
            <p className="hidden lg:block text-[10px] text-gray-600 uppercase tracking-widest px-2 pt-2 pb-1">Main</p>
            <NavItem to="/" icon={<LayoutDashboard className="w-4 h-4" />} label="Dashboard" />
            <NavItem to="/analytics" icon={<TrendingUp className="w-4 h-4" />} label="Analytics" />
            <p className="hidden lg:block text-[10px] text-gray-600 uppercase tracking-widest px-2 pt-4 pb-1">Learn</p>
            <NavItem to="/mentor" icon={<BookOpen className="w-4 h-4" />} label="Mentor School" />
            <NavItem to="/news" icon={<Newspaper className="w-4 h-4" />} label="News Room" />
            <p className="hidden lg:block text-[10px] text-gray-600 uppercase tracking-widest px-2 pt-4 pb-1">Research</p>
            <NavItem to="/lab" icon={<FlaskConical className="w-4 h-4" />} label="Pipeline Lab" />
          </nav>

          {/* Connection dot */}
          <div className="px-3 lg:px-4 py-3 border-t border-surface-3">
            <div className="flex items-center gap-2">
              <div className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-accent-green live-dot' : 'bg-accent-red'}`} />
              <span className="hidden lg:block text-[11px] text-gray-500">{connected ? 'Connected' : 'Disconnected'}</span>
            </div>
          </div>
        </aside>

        {/* Main content */}
        <main className="flex-1 overflow-y-auto">
          <Routes>
            <Route path="/" element={<Dashboard wsEvent={lastEvent} />} />
            <Route path="/analytics" element={<Analytics />} />
            <Route path="/mentor" element={<MentorSchool />} />
            <Route path="/news" element={<NewsRoom />} />
            <Route path="/lab" element={<Lab />} />
          </Routes>
        </main>
      </div>

      <AIChatBot />
    </BrowserRouter>
  )
}

function NavItem({ to, icon, label }: { to: string; icon: React.ReactNode; label: string }) {
  return (
    <NavLink
      to={to}
      end={to === '/'}
      className={({ isActive }) =>
        `flex items-center gap-2.5 px-2 py-2 rounded-lg transition-all text-sm ${
          isActive
            ? 'bg-brand/15 text-brand-glow font-medium'
            : 'text-gray-500 hover:text-gray-300 hover:bg-surface-3'
        }`
      }
    >
      {icon}
      <span className="hidden lg:block">{label}</span>
    </NavLink>
  )
}

export default App
