/**
 * Exact arithmetic over the decimal STRINGS the API sends, plus display helpers.
 *
 * Pydantic serialises `Decimal` as a string — `"87"`, `"-9"`, `"12.5"` — which
 * is the whole reason `sum(signals) == score` can be checked exactly on the
 * server with `==` and no tolerance (`contract/models.py`). Parsing those into
 * JavaScript numbers to add them up would hand that back: 0.1 + 0.2 is the
 * canonical example and a repriced condition is one seed file away from putting
 * a fractional contribution on the bar.
 *
 * So the console adds them as scaled integers. This is small on purpose — it is
 * not a decimal library, it is exactly enough to add a bar up and compare it to
 * a score. `queue.py` makes the same argument about publishing rounded factors:
 * an explanation that is approximately true is the standard §1 refuses.
 */

/** A decimal string, split into sign, digits and scale. */
function scale(value: string): number {
  const dot = value.indexOf('.')
  return dot === -1 ? 0 : value.length - dot - 1
}

function toScaled(value: string, target: number): bigint {
  const negative = value.startsWith('-')
  const body = negative ? value.slice(1) : value
  const [whole, frac = ''] = body.split('.')
  const padded = (whole + frac).padEnd(whole.length + target, '0')
  const digits = padded.slice(0, whole.length + target) || '0'
  const n = BigInt(digits || '0')
  return negative ? -n : n
}

/** Exact sum of decimal strings, returned as a decimal string. */
export function decSum(values: string[]): string {
  if (values.length === 0) return '0'
  const target = Math.max(...values.map(scale))
  let total = 0n
  for (const v of values) total += toScaled(v, target)
  return fromScaled(total, target)
}

function fromScaled(value: bigint, target: number): string {
  const negative = value < 0n
  const digits = (negative ? -value : value).toString().padStart(target + 1, '0')
  const whole = digits.slice(0, digits.length - target)
  const frac = target > 0 ? digits.slice(digits.length - target) : ''
  const body = frac ? `${whole}.${frac}` : whole
  return negative ? `-${body}` : body
}

/** Exact equality of two decimal strings, ignoring trailing-zero spelling:
 *  `"87"`, `"87.0"` and `"87.00"` are the same number and the API is free to
 *  send any of them. */
export function decEq(a: string, b: string): boolean {
  const target = Math.max(scale(a), scale(b))
  return toScaled(a, target) === toScaled(b, target)
}

/** For widths and sorting only — never for a claim about whether the bar adds
 *  up. Anything comparing contributions to a score uses `decEq`. */
export function decNum(value: string | number | null | undefined): number {
  if (value === null || value === undefined) return 0
  return typeof value === 'number' ? value : Number(value)
}

export function decAbs(value: string): string {
  return value.startsWith('-') ? value.slice(1) : value
}

/** `+34` / `-9`. The sign is information: it is the direction of the deduction. */
export function signed(value: string): string {
  return value.startsWith('-') ? value : `+${value}`
}

// ------------------------------------------------------------------ display

export function money(amount: string | number | null | undefined,
                      currency = 'USD'): string {
  if (amount === null || amount === undefined) return '—'
  const n = decNum(amount)
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency', currency, maximumFractionDigits: 2,
    }).format(n)
  } catch {
    return `${n.toFixed(2)} ${currency}`
  }
}

export function when(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toISOString().replace('T', ' ').replace(/\.\d+Z$/, 'Z').replace(/Z$/, ' UTC')
}

export function whenShort(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toISOString().slice(0, 16).replace('T', ' ')
}

/** Seconds into something a human reads, for the triage-clock tile. */
export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  const s = Math.abs(seconds)
  if (s < 90) return `${Math.round(s)}s`
  if (s < 5400) return `${(s / 60).toFixed(1)} min`
  if (s < 172800) return `${(s / 3600).toFixed(1)} h`
  return `${(s / 86400).toFixed(1)} days`
}

export const DIRECTION_CLASS: Record<string, string> = {
  aggravating: 'agg',
  mitigating: 'mit',
  veto: 'veto',
}
