import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { DecisionFrame } from './DecisionFrame'

/**
 * The plan's acceptance criterion: *a simulated decision and a real one are
 * never visually confusable.* Same bar, different frame, and `persisted` is what
 * decides which — so these assert on the frame rather than on the bar.
 */
describe('DecisionFrame', () => {
  it('marks a stored decision as something that happened', () => {
    render(<DecisionFrame persisted>body</DecisionFrame>)
    const frame = screen.getByTestId('decision-frame')
    expect(frame).toHaveAttribute('data-persisted', 'true')
    expect(frame).toHaveClass('real')
    expect(screen.getByText(/STORED DECISION/)).toBeInTheDocument()
  })

  it('marks a simulation as not persisted, in words a reader cannot miss', () => {
    render(<DecisionFrame persisted={false}>body</DecisionFrame>)
    const frame = screen.getByTestId('decision-frame')
    expect(frame).toHaveAttribute('data-persisted', 'false')
    expect(frame).toHaveClass('unreal')
    expect(frame).not.toHaveClass('real')
    expect(screen.getByText(/NOT PERSISTED/)).toBeInTheDocument()
    expect(screen.getByText(/rolled back/)).toBeInTheDocument()
  })

  it('gives a stopped charge its own frame again', () => {
    render(<DecisionFrame persisted stopped>body</DecisionFrame>)
    const frame = screen.getByTestId('decision-frame')
    expect(frame).toHaveClass('stopped')
    expect(screen.getByText(/this charge was stopped/)).toBeInTheDocument()
  })

  it('never reads a simulation as stopped, whatever else it is told', () => {
    // A rolled-back decision that would have declined a charge did not decline
    // anything. `persisted` wins over `stopped`, and it wins in the direction
    // that claims less.
    render(<DecisionFrame persisted={false} stopped>body</DecisionFrame>)
    const frame = screen.getByTestId('decision-frame')
    expect(frame).toHaveClass('unreal')
    expect(frame).not.toHaveClass('stopped')
    expect(screen.queryByText(/this charge was stopped/)).not.toBeInTheDocument()
  })
})
