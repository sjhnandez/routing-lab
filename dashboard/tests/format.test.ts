import { describe, expect, it } from 'vitest'
import {
  errorMessage,
  formatSeconds,
  formatUtc,
  formatUtcSeconds,
  shortImageDigest,
} from '../src/lib/format'

describe('errorMessage', () => {
  it('returns the message of an Error', () => {
    expect(errorMessage(new Error('boom'))).toBe('boom')
  })

  it('stringifies non-Error values and message-less Errors', () => {
    expect(errorMessage('plain string')).toBe('plain string')
    expect(errorMessage(null)).toBe('null')
    expect(errorMessage(42)).toBe('42')
    expect(errorMessage(new Error(''))).toBe('Error')
  })
})

describe('formatSeconds', () => {
  it('renders two fixed decimals with the seconds suffix', () => {
    expect(formatSeconds(12.344)).toBe('12.34s')
    expect(formatSeconds(9.6)).toBe('9.60s')
    expect(formatSeconds(0)).toBe('0.00s')
  })
})

describe('formatUtc', () => {
  it('formats an ISO timestamp as minutes-precision UTC', () => {
    expect(formatUtc('2026-10-13T08:29:31Z')).toBe('2026-10-13 08:29 UTC')
  })

  it('falls back to the raw string for invalid input', () => {
    expect(formatUtc('not-a-date')).toBe('not-a-date')
  })
})

describe('formatUtcSeconds', () => {
  it('formats an ISO timestamp as seconds-precision UTC', () => {
    expect(formatUtcSeconds('2026-10-13T08:29:31Z')).toBe('2026-10-13 08:29:31 UTC')
  })

  it('falls back to the raw string for invalid input', () => {
    expect(formatUtcSeconds('')).toBe('')
  })
})

describe('shortImageDigest', () => {
  it('shortens a sha256 digest to twelve hex characters', () => {
    const image = 'ghcr.io/berriai/litellm@sha256:67831417c141aabbccddeeff00112233'
    expect(shortImageDigest(image)).toBe('sha256:67831417c141')
  })

  it('returns the input unchanged when no digest is present', () => {
    expect(shortImageDigest('litellm:latest')).toBe('litellm:latest')
  })
})
