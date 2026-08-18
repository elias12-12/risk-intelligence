/**
 * The grouping keeps the same three rules `copy/tiles.ts` keeps for the copy.
 *
 * The one that needs a test is RULE 1: a tile this file has never heard of does
 * not vanish. Grouping is the first thing in this console that decides whether a
 * payload field is rendered at all, and "the tenth tile shipped and nobody
 * noticed for a month" is the failure it can produce silently.
 */
import { describe, expect, it } from 'vitest'

import type { KpiTile } from '../api/types'
import { OTHER_GROUP, TILE_COPY, TILE_GROUPS, groupTiles } from './tiles'

function tile(key: string): KpiTile {
  return {
    key,
    label: key,
    value: '1',
    unit: 'count',
    numerator: null,
    denominator: null,
    window_start: '2026-01-08T15:00:00Z',
    window_end: '2026-01-15T15:00:00Z',
    baseline_start: null,
    baseline_end: null,
    baseline_value: null,
    delta_pct: null,
    basis: 'a basis',
    requires: 'a milestone',
    synthetic: false,
    caveat: null,
    parts: [],
  }
}

const KNOWN = Object.keys(TILE_COPY)

describe('grouping the tiles', () => {
  it('files every tile this console can explain', () => {
    // The two maps are keyed the same way and describe the same nine tiles. A
    // tile with plain-language copy but no section would render under "not yet
    // categorised" next to eight tiles that were filed, which reads as a bug in
    // the payload rather than an omission here.
    const grouped = groupTiles(KNOWN.map(tile))
    const uncategorised = grouped.find((g) => g.group.key === OTHER_GROUP.key)
    expect(uncategorised).toBeUndefined()
  })

  it('drops nothing and duplicates nothing', () => {
    const input = [...KNOWN, 'a_tile_from_the_future'].map(tile)
    const out = groupTiles(input).flatMap((g) => g.tiles.map((t) => t.key))
    expect(out.sort()).toEqual(input.map((t) => t.key).sort())
  })

  it('shows a tile it has never heard of rather than hiding it', () => {
    // Rule 1, the whole reason the map is keyed rather than positional.
    const grouped = groupTiles([tile('a_tile_from_the_future')])
    expect(grouped).toHaveLength(1)
    expect(grouped[0].group).toBe(OTHER_GROUP)
    expect(grouped[0].tiles.map((t) => t.key)).toEqual(['a_tile_from_the_future'])
  })

  it('renders no empty section', () => {
    const grouped = groupTiles([tile('alert_volume')])
    expect(grouped).toHaveLength(1)
    expect(grouped[0].tiles).toHaveLength(1)
  })

  it('keeps the server order inside a section', () => {
    // The sections are a reading order the console chose; the order WITHIN one
    // is still the payload's, because `kpis.v1` emits its tiles in an order and
    // resorting them here would be a second opinion about it.
    const grouped = groupTiles([tile('rule_precision'), tile('false_positive_rate')])
    expect(grouped[0].tiles.map((t) => t.key))
      .toEqual(['rule_precision', 'false_positive_rate'])
  })

  it('gives every section a question it answers', () => {
    for (const g of [...TILE_GROUPS, OTHER_GROUP]) {
      expect(g.label.length, g.key).toBeGreaterThan(0)
      expect(g.answers.length, g.key).toBeGreaterThan(0)
    }
  })
})
