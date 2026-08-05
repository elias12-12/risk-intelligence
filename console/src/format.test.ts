/**
 * The bar adds up EXACTLY, or the claim is not the claim.
 *
 * `sum(signals) == score` is enforced on the server with `Decimal` and `==` and
 * no tolerance. The console receives those numbers as strings and would give the
 * guarantee back the moment it parsed them into floats — so these tests are
 * against the arithmetic, not against a rendering.
 */
import { describe, expect, it } from 'vitest'

import { decEq, decSum, signed } from './format'

describe('exact decimal arithmetic', () => {
  it('sums the shipped fixture bar to its published score', () => {
    // TXN-48291: 34 + 21 + 18 + 14 = 87.
    expect(decSum(['34', '21', '18', '14'])).toBe('87')
    expect(decEq(decSum(['34', '21', '18', '14']), '87')).toBe(true)
  })

  it('sums a pool with mitigators, which is the case that matters', () => {
    // TXN-48251 after seed 0026: 12 − 9 − 6 − 4 = −7, which is why
    // consolidation drops the pool rather than publishing a negative score.
    expect(decSum(['12', '-9', '-6', '-4'])).toBe('-7')
  })

  it('is exact where float addition is not', () => {
    // 0.1 + 0.2 !== 0.3 in IEEE 754. A repriced condition is one seed file away
    // from putting a fractional contribution on the bar, and on that day a
    // float sum would render "does not add up" against a payload that does.
    expect(0.1 + 0.2).not.toBe(0.3)
    expect(decSum(['0.1', '0.2'])).toBe('0.3')
    expect(decEq(decSum(['0.1', '0.2']), '0.30')).toBe(true)
  })

  it('adds across differing scales without losing precision', () => {
    expect(decSum(['1.005', '2.1', '3'])).toBe('6.105')
    expect(decSum(['-0.001', '0.001'])).toBe('0.000')
    expect(decEq(decSum(['-0.001', '0.001']), '0')).toBe(true)
  })

  it('treats trailing-zero spellings of the same number as equal', () => {
    // The server is free to send "87", "87.0" or "87.00" for the same Decimal.
    expect(decEq('87', '87.0')).toBe(true)
    expect(decEq('87', '87.00')).toBe(true)
    expect(decEq('87', '87.01')).toBe(false)
  })

  it('sums an empty pool to zero, which is a real answer', () => {
    expect(decSum([])).toBe('0')
    expect(decEq(decSum([]), '0')).toBe(true)
  })

  it('keeps the sign, because the sign is the direction of the deduction', () => {
    expect(signed('12')).toBe('+12')
    expect(signed('-9')).toBe('-9')
    expect(signed('0')).toBe('+0')
  })
})
