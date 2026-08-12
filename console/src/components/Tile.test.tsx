import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { KpiWindow, TileView } from './Tile'
import type { KpiSet, KpiTile } from '../api/types'
import { TILE_COPY } from '../copy/tiles'

/** `contract/kpis.py`'s constant, verbatim. If the server rewords it this test
 *  fails, which is the point: the console's rewrite is keyed on the text. */
const SYNTHETIC_GROUND_TRUTH =
  'Measured against transactions.synthetic_label — fraud this repository planted. '
  + 'Exact on this dataset and meaningless beyond it. In production recall is not '
  + 'measurable without a random-sample audit of unalerted traffic.'

function tile(over: Partial<KpiTile> = {}): KpiTile {
  return {
    key: 'false_negative_rate',
    label: 'False-negative rate',
    value: '95',
    unit: 'percent',
    numerator: 76,
    denominator: 80,
    window_start: '2026-01-08T15:00:00Z',
    window_end: '2026-01-15T15:00:00Z',
    baseline_start: null,
    baseline_end: null,
    baseline_value: null,
    delta_pct: null,
    basis: 'labelled-fraud decisions with no case raised, over labelled-fraud decisions',
    requires: '§11 + the generator label',
    synthetic: true,
    caveat: 'exact against planted ground truth and meaningless beyond it',
    parts: [],
    ...over,
  }
}

describe('KPI tiles', () => {
  it('explains a known tile in plain language rather than in its basis string', () => {
    // Session 6 §6. The payload's `basis` is written for whoever maintains the
    // view; `requires` names the internal milestone that made the tile possible.
    // Both stay on the wire — `requires` is part of kpis.v1 — and neither is
    // what an audience should be reading.
    render(<TileView tile={tile()} />)
    expect(screen.getByText(TILE_COPY.false_negative_rate.means)).toBeInTheDocument()
    expect(screen.getByText(TILE_COPY.false_negative_rate.computed)).toBeInTheDocument()
    expect(screen.queryByText(/requires/)).not.toBeInTheDocument()
  })

  it('falls back to the payload when it has never heard of the tile', () => {
    // The fallback is the whole reason the copy is keyed rather than positional:
    // a tenth tile added server-side arrives explaining itself.
    const t = tile({ key: 'a_tile_from_the_future' })
    render(<TileView tile={t} />)
    expect(screen.getByText(t.basis)).toBeInTheDocument()
  })

  it('keeps every caveat, in plainer words', () => {
    const t = tile({ caveat: SYNTHETIC_GROUND_TRUTH })
    render(<TileView tile={t} />)
    expect(screen.getByText(/We planted this fraud/)).toBeInTheDocument()
    expect(screen.queryByText(/transactions\.synthetic_label/)).not.toBeInTheDocument()
  })

  it('rewrites a caveat it does not recognise to itself', () => {
    // Rewrites are replacements over the string, so an unrecognised sentence
    // passes through unchanged. A caveat that could be dropped by failing to
    // match is worse than an ugly one.
    const t = tile({ caveat: 'something the console has never seen' })
    render(<TileView tile={t} />)
    expect(screen.getByText(/something the console has never seen/)).toBeInTheDocument()
  })

  it('keeps the derived counts in the provenance note', () => {
    // 0029's argument: the note is DERIVED because a hardcoded one became false
    // the moment a real analyst wrote a verdict. Plainer words, same numbers.
    const t = tile({
      synthetic: true,
      caveat: "4 of 7 dispositioned cases in this window carry a verdict written by "
        + "scripts/resolve_actions.py rather than by an analyst; 3 carry a person's.",
    })
    render(<TileView tile={t} />)
    expect(screen.getByText(/4 of 7 verdicts here were written by a script/))
      .toBeInTheDocument()
  })

  it('never shows a rate without its denominator', () => {
    render(<TileView tile={tile()} />)
    expect(screen.getByText('95%')).toBeInTheDocument()
    expect(screen.getByText('76 / 80')).toBeInTheDocument()
  })

  it('says a number is synthetic when the payload says so', () => {
    render(<TileView tile={tile({ synthetic: true })} />)
    expect(screen.getByText(/synthetic/)).toBeInTheDocument()
  })

  it('refuses to imply a prior period when there is no baseline', () => {
    // §11's "console copy that outruns the system": deltas implying a measured
    // prior period. A delta renders only when delta_pct is non-null.
    render(<TileView tile={tile({ delta_pct: null })} />)
    expect(screen.getByText('no prior window')).toBeInTheDocument()
  })

  it('names the baseline window on every delta it does render', () => {
    render(<TileView tile={tile({
      delta_pct: '-41',
      baseline_start: '2026-01-01T15:00:00Z',
      baseline_end: '2026-01-08T15:00:00Z',
    })} />)
    expect(screen.getByText(/-41%/)).toBeInTheDocument()
    expect(screen.getByText(/vs 2026-01-01/)).toBeInTheDocument()
  })

  it('renders an absent value as an absence, not as a zero', () => {
    // Decision 17's argument, applied to a tile: a number with no denominator
    // behind it is worse than no number.
    render(<TileView tile={tile({ value: null, numerator: null, denominator: null })} />)
    expect(screen.getByText(/nothing in this window/)).toBeInTheDocument()
    expect(screen.queryByText('0%')).not.toBeInTheDocument()
  })

  it('gives each part its own denominator', () => {
    // §11's block / challenge / fail-open rate is one tile covering three rates
    // whose denominators differ; sharing one would be the exact error the
    // denominator rule exists to prevent.
    render(<TileView tile={tile({
      value: null,
      parts: [
        { label: 'challenge rate', value: '4', unit: 'count', numerator: 4, denominator: 9923,
          baseline_value: null, delta_pct: null },
        { label: 'block rate', value: '0', unit: 'count', numerator: 0, denominator: 9923,
          baseline_value: null, delta_pct: null },
      ],
    })} />)
    expect(screen.getByText('(4/9923)')).toBeInTheDocument()
    expect(screen.getByText('(0/9923)')).toBeInTheDocument()
  })
})

describe('the KPI window header', () => {
  function set(over: Partial<KpiSet> = {}): KpiSet {
    return {
      as_of: '2026-01-15T15:00:00Z',
      window_start: '2026-01-08T15:00:00Z',
      window_end: '2026-01-15T15:00:00Z',
      baseline_start: '2026-01-01T15:00:00Z',
      baseline_end: '2026-01-08T15:00:00Z',
      baseline_available: true,
      baseline_absent_reason: null,
      tiles: [],
      ...over,
    }
  }

  it('names the comparison window when there is one', () => {
    render(<KpiWindow set={set()} />)
    expect(screen.getByText(/immediately preceding window of the same length/)).toBeInTheDocument()
  })

  it('prints the payload\'s reason when there is not', () => {
    render(<KpiWindow set={set({
      baseline_available: false,
      baseline_start: null,
      baseline_end: null,
      baseline_absent_reason: 'the dataset starts 2026-01-01, inside this window',
    })} />)
    expect(screen.getByText(/the dataset starts 2026-01-01, inside this window/))
      .toBeInTheDocument()
  })
})
