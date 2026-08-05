import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ScoreBar } from './ScoreBar'
import type { Signal } from '../api/types'

function signal(over: Partial<Signal> & Pick<Signal, 'contribution' | 'direction'>): Signal {
  return {
    rank: 1,
    feature_key: 'card_cnp_count',
    human_text: 'a sentence an analyst can act on',
    reason_code: null,
    source_rule_id: 'R-114',
    asserted_by_rules: ['R-114'],
    feature_value: null,
    value_as_of: null,
    value_computed_at: null,
    ...over,
  }
}

// TXN-48291's actual bar.
const BURST: Signal[] = [
  signal({ rank: 1, feature_key: 'card_cnp_count', contribution: '34', direction: 'aggravating' }),
  signal({ rank: 2, feature_key: 'device_first_seen_min', contribution: '21', direction: 'aggravating' }),
  signal({ rank: 3, feature_key: 'session_geo_jump_km', contribution: '18', direction: 'aggravating' }),
  signal({ rank: 4, feature_key: 'mcc_is_new_for_customer', contribution: '14', direction: 'aggravating' }),
]

describe('ScoreBar', () => {
  it('shows the arithmetic when the signals sum to the score', () => {
    render(<ScoreBar signals={BURST} score="87" />)
    expect(screen.getByTestId('bar-sum')).toHaveTextContent('+34 +21 +18 +14 = 87')
    expect(screen.getByTestId('bar-sum')).not.toHaveClass('notice')
  })

  it('says so when a payload does not add up, rather than rendering it quietly', () => {
    // Unreachable through the API — AlertDetail's validator raises and the
    // endpoint returns 500. Rendered anyway, because the one surface worth
    // restating an invariant on is the one whose whole proposition is that it
    // holds.
    render(<ScoreBar signals={BURST} score="90" />)
    expect(screen.getByTestId('bar-sum')).toHaveTextContent(
      'signals sum to 87 but the score is 90')
    expect(screen.getByTestId('bar-sum')).toHaveClass('notice', 'bad')
  })

  it('never hides a mitigator', () => {
    // §13 constraint 3: an explanation that lists only aggravators is wrong even
    // when every line is true. A bar that sorted by magnitude or truncated to
    // the top three would break that silently — so every signal renders, in
    // rank order, whatever its sign.
    const pool: Signal[] = [
      signal({ rank: 1, feature_key: 'country_is_new_for_customer', contribution: '12', direction: 'aggravating' }),
      signal({ rank: 2, feature_key: 'recent_travel_purchase', contribution: '-9', direction: 'mitigating' }),
      signal({ rank: 3, feature_key: 'device_known_for_customer', contribution: '-6', direction: 'mitigating' }),
      signal({ rank: 4, feature_key: 'entry_mode_chip_pin', contribution: '-4', direction: 'mitigating' }),
    ]
    render(<ScoreBar signals={pool} score="-7" />)

    expect(screen.getByTestId('bar-sum')).toHaveTextContent('+12 -9 -6 -4 = -7')
    expect(screen.getAllByText('mitigating')).toHaveLength(3)
    expect(screen.getByText('recent_travel_purchase')).toBeInTheDocument()
  })

  it('renders a veto signal, which carries no points and changes the outcome', () => {
    const pool: Signal[] = [
      signal({ rank: 1, contribution: '68', direction: 'aggravating' }),
      signal({ rank: 2, feature_key: null, contribution: '0', direction: 'veto',
               human_text: 'T-021 established a veto: confirmed travel' }),
    ]
    render(<ScoreBar signals={pool} score="68" />)
    expect(screen.getByText('veto')).toBeInTheDocument()
    expect(screen.getByTestId('bar-sum')).toHaveTextContent('= 68')
  })

  it('says the bar is empty rather than drawing nothing', () => {
    // TXN-48251: score 0, no signals, and that IS the answer.
    render(<ScoreBar signals={[]} score="0" />)
    expect(screen.getByLabelText('empty score bar')).toBeInTheDocument()
    expect(screen.getByText(/nothing to show on the bar/)).toBeInTheDocument()
  })
})
