import { NavLink, Navigate, Route, Routes, useMatch } from 'react-router-dom'

import { SystemStrip } from './components/SystemStrip'
import { SignIn } from './components/SignIn'
import { AlertScreen } from './screens/Alert'
import { AuthorizeScreen } from './screens/Authorize'
import { DashboardScreen } from './screens/Dashboard'
import { RuleAuthorScreen } from './screens/RuleAuthor'
import { RuleDetailScreen } from './screens/RuleDetail'
import { RulesScreen } from './screens/Rules'
import { SimulateScreen } from './screens/Simulate'
import { useSession } from './session'

export function App() {
  const { can } = useSession()

  // `NavLink to="/"` without `end` matches every route, and with `end` it stops
  // matching the dashboard's own second tab. Neither is what "am I looking at
  // the dashboard" means now that it has sub-tabs and a case detail hanging off
  // it, so the three routes that ARE the dashboard are named.
  //
  // THREE SEPARATE CALLS, AND NOT `a() || b() || c()`. `||` short-circuits, so
  // folding these into one expression calls one hook on `/` and three on every
  // other route — a conditional hook, and React tears the whole tree down with
  // "rendered more hooks than during the previous render" the moment you click
  // a tab. The symptom is not a warning in a log: the app goes blank, and it
  // does so only on the SECOND render, which is why a hard refresh looks like
  // it fixed it.
  const atQueue = useMatch('/')
  const atMeasurement = useMatch('/measurement')
  const atCase = useMatch('/alerts/:id')
  const onDashboard = atQueue !== null || atMeasurement !== null || atCase !== null

  return (
    <div className="shell">
      {/* First thing in the tab order, visible only once focused. The console's
          densest surface is a table; a keyboard user should not have to walk the
          navigation to reach it on every page. */}
      <a className="skip" href="#main">Skip to content</a>

      <header className="topbar">
        <div className="brand">GlassBox <small>console</small></div>
        <nav className="nav" aria-label="sections">
          {/* `/measurement` is a child of the dashboard route, so it lights this
              tab too — which is the point of having merged them. */}
          <NavLink to="/" className={onDashboard ? 'on' : ''}>Dashboard</NavLink>
          <Tab to="/rules">Rules</Tab>
          <Tab to="/simulate">Simulate</Tab>
        </nav>
        {/* The one control here that WRITES sits with the identity that
            authorises it, on the other side of the bar from the read-only
            surfaces. A verb that can decline a real charge should not be one
            tab-stop away from a verb that renders a table. */}
        {can('admin') && (
          <nav className="nav-write" aria-label="actions">
            <Tab to="/authorize" admin>Send a charge</Tab>
          </nav>
        )}
        <SignIn />
      </header>

      <SystemStrip />

      <main id="main" tabIndex={-1}>
        <Routes>
          {/* A layout route: the dashboard shell stays mounted while its two
              sub-tabs change, so the KPI window and the held payloads survive a
              move between them. See screens/Dashboard.tsx. */}
          <Route path="/" element={<DashboardScreen />}>
            <Route index element={null} />
            <Route path="measurement" element={null} />
          </Route>
          {/* The tiles used to live here. Kept as a redirect rather than
              deleted: links to `/kpis` are in the walkthrough and in a deck. */}
          <Route path="/kpis" element={<Navigate to="/measurement" replace />} />
          <Route path="/alerts/:id" element={<AlertScreen />} />
          <Route path="/rules" element={<RulesScreen />} />
          <Route path="/rules/new" element={<RuleAuthorScreen />} />
          <Route path="/rules/:id" element={<RuleDetailScreen />} />
          <Route path="/rules/:id/edit" element={<RuleAuthorScreen />} />
          <Route path="/simulate" element={<SimulateScreen />} />
          <Route path="/authorize" element={<AuthorizeScreen />} />
        </Routes>
      </main>
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
