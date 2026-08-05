import { NavLink, Route, Routes } from 'react-router-dom'

import { SystemStrip } from './components/SystemStrip'
import { SignIn } from './components/SignIn'
import { AlertScreen } from './screens/Alert'
import { AuthorizeScreen } from './screens/Authorize'
import { KpiScreen } from './screens/Kpis'
import { QueueScreen } from './screens/Queue'
import { RuleAuthorScreen } from './screens/RuleAuthor'
import { RuleDetailScreen } from './screens/RuleDetail'
import { RulesScreen } from './screens/Rules'
import { SimulateScreen } from './screens/Simulate'
import { useSession } from './session'

export function App() {
  const { can } = useSession()

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">GlassBox <small>console</small></div>
        <nav className="nav">
          <Tab to="/" end>Queue</Tab>
          <Tab to="/kpis">Measurement</Tab>
          <Tab to="/rules">Rules</Tab>
          <Tab to="/simulate">Simulate</Tab>
          {can('admin') && <Tab to="/authorize" admin>Send a charge</Tab>}
        </nav>
        <SignIn />
      </header>

      <SystemStrip />

      <Routes>
        <Route path="/" element={<QueueScreen />} />
        <Route path="/alerts/:id" element={<AlertScreen />} />
        <Route path="/kpis" element={<KpiScreen />} />
        <Route path="/rules" element={<RulesScreen />} />
        <Route path="/rules/new" element={<RuleAuthorScreen />} />
        <Route path="/rules/:id" element={<RuleDetailScreen />} />
        <Route path="/rules/:id/edit" element={<RuleAuthorScreen />} />
        <Route path="/simulate" element={<SimulateScreen />} />
        <Route path="/authorize" element={<AuthorizeScreen />} />
      </Routes>
    </div>
  )
}

function Tab({ to, end, admin, children }: {
  to: string; end?: boolean; admin?: boolean; children: React.ReactNode
}) {
  return (
    <NavLink to={to} end={end}
             className={({ isActive }) =>
               `${isActive ? 'on' : ''}${admin ? ' admin' : ''}`.trim()}>
      {children}
    </NavLink>
  )
}
