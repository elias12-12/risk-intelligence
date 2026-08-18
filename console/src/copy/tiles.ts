/**
 * Plain-language copy for the nine tiles — the ONE place this console writes
 * words of its own about a number.
 *
 * `Tile.tsx` opens by saying no tile copy is written in it, and that rule is
 * still right: a description sitting next to a number it does not come from
 * will agree with it until the day the view changes. This file does not break
 * the rule so much as name the exception and fence it.
 *
 * WHY THE EXCEPTION EXISTS. The server's `basis` strings are correct and were
 * written for whoever has to maintain the view — *"cases raised or restated,
 * over every decision evaluated; a folded evaluation is the same case seen
 * again and is not counted twice"* — and `requires` is archaeology: which
 * internal milestone made the tile possible. Neither means anything to an
 * audience. The choice was console-side or server-side (session plan §14 Q5);
 * console-side was taken, because replacing the `basis` strings changes
 * `kpis.v1`'s bytes and spends a published contract's stability on wording.
 *
 * THE THREE RULES THIS FILE KEEPS.
 *
 *  1. **Keyed on `tile.key`, with the payload as the fallback.** A tile this
 *     file has never heard of renders its own `basis` rather than nothing. A
 *     new server-side tile explains itself on the day it ships.
 *  2. **Nothing here is a number, a denominator or a window.** Every quantity
 *     on screen still comes off the payload. These are definitions, not values,
 *     which is why they cannot drift into disagreeing with the arithmetic.
 *  3. **The caveats are rewritten, never dropped.** `plainCaveat` shortens the
 *     sentences and keeps every one of them, including the derived provenance
 *     note WITH its counts — see below.
 */

import type { KpiTile } from '../api/types'

/** What the tile means, and how the number was arrived at. */
export interface TileCopy {
  means: string
  computed: string
}

export const TILE_COPY: Record<string, TileCopy> = {
  alert_volume: {
    means: 'How many cases came out of how much traffic.',
    computed: 'Cases raised or restated ÷ every decision evaluated in the window. '
      + 'A repeat of the same case is not counted twice.',
  },
  score_distribution: {
    means: "The shape of the population's risk.",
    computed: 'Decisions grouped by subject type and band. The headline is how '
      + 'many scored above zero.',
  },
  false_positive_rate: {
    means: 'How often we alerted on something a human said was fine.',
    computed: '(False positive + confirmed legitimate) ÷ cases with any verdict. '
      + '"Inconclusive" is excluded from both sides.',
  },
  false_negative_rate: {
    means: 'How much known fraud we missed.',
    computed: 'Labelled fraud that never became a case ÷ all labelled fraud.',
  },
  validation_outcomes: {
    means: 'What humans concluded about our alerts.',
    computed: 'One verdict per case, counted by verdict.',
  },
  median_triage_time: {
    means: 'How long a case waits before someone works it.',
    computed: 'From when the event happened to the first verdict on it.',
  },
  action_rates: {
    means: 'How often we acted on a customer, and how it went.',
    computed: 'Preventive actions ÷ decisions evaluated. Counted per action '
      + 'issued, not per decision — one step-up per customer, not one per re-check.',
  },
  rule_precision: {
    means: 'Which rules are worth their alerts.',
    computed: 'Cases a rule cited evidence on that were confirmed fraud ÷ cases '
      + 'it cited evidence on with any verdict.',
  },
  emerging_trends: {
    means: 'Which fraud patterns are moving.',
    computed: 'Distinct reason codes cited by cases in the window, each compared '
      + 'with the window immediately before it.',
  },
}

/**
 * The caveat sentences, in plainer words.
 *
 * Applied as successive replacements over the payload's string rather than as a
 * lookup, because one tile's caveat is two of these concatenated
 * (`action_rates` carries the outcomes note and the fail-mode note), and a
 * lookup would have to know that.
 *
 * The provenance note keeps its CAPTURED NUMBERS. It is derived server-side for
 * a reason recorded in migration 0029: it used to be a hardcoded sentence
 * claiming every verdict here was a script's, which became false the moment a
 * real analyst wrote one. Rewriting the words is fine; re-asserting the fact
 * would reintroduce exactly the defect 0029 removed.
 */
const CAVEAT_REWRITES: Array<[RegExp, string]> = [
  [/Measured against transactions\.synthetic_label[^]*?random-sample audit of unalerted traffic\./,
    'We planted this fraud, so we can measure whether we caught it. Real data has '
    + 'no answer key — there, recall needs a sampled audit of traffic we never alerted on.'],

  [/Challenge outcomes were settled by scripts\/resolve_actions\.py[^]*?stamped synthetic\./,
    'No customer answered these step-ups; a script settled them. Every such row '
    + 'is marked synthetic.'],

  [/fail_mode records the lane's POLICY[^]*?measured resilience number\./,
    "This is the lane's policy, not an observed failure. Nothing has failed, "
    + 'because nothing real has run.'],

  // The derived provenance note, both of its shapes. The counts are captured and
  // replayed; they are the part that is measured.
  [/(\d+) of (\d+) dispositioned cases in this window carry a verdict written by [^]*?carry a person's\./,
    '$1 of $2 verdicts here were written by a script rather than by an analyst.'],
  [/Every dispositioned case in this window carries a verdict written by [^]*?source = 'synthetic'\)\./,
    'Every verdict here was written by a script, not by an analyst.'],

  [/A rule that asserted evidence on a case another rule CARRIED[^]*?this tile uses `asserted`\./,
    'A rule that argued for a case another rule ended up carrying is not thereby '
    + 'wrong. This tile counts the rules that argued.'],
]

/**
 * The payload's caveat, in plainer words — or unchanged when this file does not
 * recognise it. Never null when the payload gave one.
 */
export function plainCaveat(caveat: string | null | undefined): string | null {
  if (!caveat) return null
  let out = caveat
  for (const [pattern, replacement] of CAVEAT_REWRITES) {
    out = out.replace(pattern, replacement)
  }
  return out.trim()
}

// ---------------------------------------------------------------- grouping

/**
 * The nine tiles, filed under what they are for.
 *
 * A flat grid of nine tiles makes nine equally-weighted claims and leaves the
 * reader to work out which two are about the same thing. Grouping is the same
 * exception this file already names — words the console writes about a number,
 * never a number — and it keeps the same three rules. In particular RULE 1: the
 * map is keyed on `tile.key` and a tile it has never heard of does not vanish,
 * it lands in `OTHER` and renders in full. A tenth tile added server-side
 * arrives on screen the day it ships, filed under "not yet categorised", which
 * is honest about what the console knows rather than silent.
 *
 * The order below is the order the sections render in, and it is an argument:
 * what the engine produced, then whether it was right, then what it cost a
 * person, then what it did to a customer. A reader who stops after the second
 * section has still read the two that decide whether any of it was worth doing.
 */
export interface TileGroup {
  key: string
  label: string
  /** The question this section answers, in one line. */
  answers: string
}

export const TILE_GROUPS: TileGroup[] = [
  {
    key: 'output',
    label: 'Detection output',
    answers: 'How much the engine raised, out of how much it looked at.',
  },
  {
    key: 'accuracy',
    label: 'Are we right?',
    answers: 'Of what we raised, how much was worth raising — and what we missed.',
  },
  {
    key: 'work',
    label: 'Analyst work',
    answers: 'How long a case waits for a person, and what people concluded.',
  },
  {
    key: 'impact',
    label: 'Customer impact and emerging risk',
    answers: 'What we did to a customer, and which patterns are moving.',
  },
]

/** Where a tile this file has never heard of goes. Never hidden. */
export const OTHER_GROUP: TileGroup = {
  key: 'other',
  label: 'Not yet categorised',
  answers: 'Tiles this console has not been taught to file. They render in full, '
    + 'explained by their own payload.',
}

const GROUP_OF: Record<string, string> = {
  alert_volume: 'output',
  score_distribution: 'output',

  false_positive_rate: 'accuracy',
  false_negative_rate: 'accuracy',
  rule_precision: 'accuracy',

  median_triage_time: 'work',
  validation_outcomes: 'work',

  action_rates: 'impact',
  emerging_trends: 'impact',
}

export interface GroupedTiles {
  group: TileGroup
  tiles: KpiTile[]
}

/**
 * The payload's tiles, in sections. Preserves the server's order WITHIN each
 * section and drops nothing: every tile handed in comes back out exactly once,
 * and a group with no tiles in this payload is not rendered at all.
 */
export function groupTiles(tiles: KpiTile[]): GroupedTiles[] {
  const out: GroupedTiles[] = []
  for (const group of [...TILE_GROUPS, OTHER_GROUP]) {
    const mine = tiles.filter((t) =>
      (GROUP_OF[t.key] ?? OTHER_GROUP.key) === group.key)
    if (mine.length > 0) out.push({ group, tiles: mine })
  }
  return out
}
