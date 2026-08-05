/**
 * Claims this console makes about itself, checked against its own source.
 *
 * The project's habit is to enforce a claim mechanically rather than assert it
 * in prose: a psycopg hook fails the extension tests on any DDL, a cursor hook
 * fails the explanation tests on a relation outside the allow-list, a grep test
 * fails the suite on an `ON CONFLICT` against `feature_values`. These are the
 * console's version. Each one guards a property that is otherwise re-established
 * by a human noticing.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

const ROOT = join(__dirname)

function sources(): Array<[string, string]> {
  const out: Array<[string, string]> = []
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const path = join(dir, entry)
      if (statSync(path).isDirectory()) { walk(path); continue }
      if (!/\.(ts|tsx)$/.test(entry)) continue
      if (/\.test\.tsx?$/.test(entry)) continue
      if (entry === 'schema.d.ts') continue        // generated
      out.push([path.slice(ROOT.length + 1).replace(/\\/g, '/'), readFileSync(path, 'utf8')])
    }
  }
  walk(ROOT)
  return out
}

const FILES = sources()

function containing(needle: string | RegExp): string[] {
  const hit = typeof needle === 'string'
    ? (text: string) => text.includes(needle)
    : (text: string) => needle.test(text)
  return FILES.filter(([, text]) => hit(text)).map(([name]) => name)
}

describe('one bar, three payloads', () => {
  it('has exactly one implementation of the score bar', () => {
    // `alert.v1`, `simulation.v1` and `ingest.v1` share Signal/Action/Evidence
    // by design so that ONE component renders all three. A second bar is the
    // failure mode decision 13 exists to prevent — `persist.ranked_signals` was
    // made public precisely so a simulated bar and a stored bar could not
    // disagree — and building one here would reintroduce it above the layer the
    // server's tests can see.
    expect(containing('bar-seg')).toEqual(['components/ScoreBar.tsx'])
  })

  it('renders all three payloads through it', () => {
    const users = containing('<ScoreBar').sort()
    expect(users).toContain('screens/Alert.tsx')        // alert.v1
    expect(users).toContain('screens/Authorize.tsx')    // ingest.v1
    expect(users).toContain('screens/Simulate.tsx')     // simulation.v1
  })
})

describe('the endpoint that commits has one call site', () => {
  it('only the authorize screen calls POST /authorize', () => {
    // The plan's trap: *the console must never call `/authorize` to "test"
    // anything.* It commits, it can decline a charge and it issues a step-up.
    // `/simulate/transaction` is the endpoint that answers a hypothetical, and
    // the two are separate endpoints so that a typo cannot turn one into the
    // other — which stops being true the moment a second screen calls it.
    expect(containing('api.authorize(')).toEqual(['screens/Authorize.tsx'])
  })

  it('the simulator never calls it', () => {
    const [, simulate] = FILES.find(([name]) => name === 'screens/Simulate.tsx')!
    expect(simulate).not.toContain('api.authorize')
    expect(simulate).toContain('api.simulateTransaction')
  })
})

describe('liveness has exactly one source', () => {
  it('nothing hardcodes a running scheduler', () => {
    // Invariant 8. A console indicator that is a hardcoded `true` is the same
    // unearned claim §11 refuses on a tile, in the one place a viewer has no way
    // to check it.
    for (const [name, text] of FILES) {
      expect(text, name).not.toMatch(/scheduler_running\s*[:=]\s*true/)
      expect(text, name).not.toMatch(/scheduler_running=\{true\}/)
    }
  })

  it('only the system strip renders a status dot', () => {
    expect(containing(/className=(\{`|")dot /)).toEqual(['components/SystemStrip.tsx'])
  })

  it('the strip reads /cycle and computes nothing of its own', () => {
    const [, strip] = FILES.find(([name]) => name === 'components/SystemStrip.tsx')!
    expect(strip).toContain('useCycle')
    // No fallback: if there is no payload, there is no answer.
    expect(strip).toMatch(/if \(!state\)/)
  })
})

describe('no console copy outruns the system', () => {
  // §11 names two strings in the old console that asserted things which were not
  // true. Neither was ever written here, and these keep it that way.
  const FORBIDDEN: Array<[RegExp, string]> = [
    [/feeds?\s+the\s+next\s+model\s+retrain/i,
      'there is no model and nothing retrains; alert_signals.source_model has never been written'],
    [/vs\.?\s+last\s+(week|month|period)/i,
      'a delta must name the baseline window the payload gave it, not a period the UI invented'],
    [/compared\s+to\s+(last|the\s+previous)\s+(week|month|period)/i,
      'same — the baseline comes off KpiTile.baseline_start, or there is no delta'],
    [/\b(AI|LLM|GPT)[-\s]?(powered|assisted|generated)\b/i,
      'no language model is involved in any field of any payload'],
  ]

  // Deliberately NOT forbidden: the string "model-backed". `CopilotResponse`
  // publishes `model_backed`, and rendering that field is the console reporting
  // what the payload says rather than claiming anything. A scan that banned the
  // word would ban the transparency it exists to protect.

  it('contains none of the claims §11 flags', () => {
    for (const [name, text] of FILES) {
      for (const [pattern, why] of FORBIDDEN) {
        expect(pattern.test(text), `${name}: ${why}`).toBe(false)
      }
    }
  })
})

describe('payload shapes are never redeclared', () => {
  it('types.ts is the only place a contract shape is named', () => {
    // Every export in types.ts is an alias into the generated schema. A
    // hand-written `interface Signal` somewhere in a screen would be a second
    // definition of a published shape, agreeing with the first until the day it
    // did not.
    for (const [name, text] of FILES) {
      if (name === 'api/types.ts') continue
      expect(text, name).not.toMatch(/^\s*(export\s+)?interface\s+(Signal|Action|Evidence|Subject|AlertDetail|QueueEntry|KpiTile)\b/m)
    }
  })

  it('declares exactly one shape by hand, and says why', () => {
    const [, types] = FILES.find(([name]) => name === 'api/types.ts')!
    // `GET /cycle` has no response_model, so OpenAPI types it as an open object
    // and the generator can only say so. It is the one exception, and the file
    // marks it as one.
    const declared = types.match(/^export interface (\w+)/gm) ?? []
    expect(declared).toEqual(['export interface CycleState', 'export interface Principal'])
  })
})
