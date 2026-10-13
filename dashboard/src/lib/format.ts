/** Human-readable text for an unknown thrown value. */
export function errorMessage(err: unknown): string {
  if (err instanceof Error && err.message) return err.message
  return String(err)
}

/** "12.34s" — fixed two decimals for the monospace timing columns. */
export function formatSeconds(seconds: number): string {
  return `${seconds.toFixed(2)}s`
}

/** "2026-10-13 08:29 UTC" from an ISO timestamp; falls back to the raw string. */
export function formatUtc(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}` +
    ` ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())} UTC`
  )
}

/** "2026-10-13 08:29:31 UTC" from an ISO timestamp; falls back to the raw string. */
export function formatUtcSeconds(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}` +
    ` ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())} UTC`
  )
}

/** "sha256:67831417c141" — short digest for the pinned-image chip. */
export function shortImageDigest(image: string): string {
  const match = /sha256:([0-9a-f]+)/.exec(image)
  return match ? `sha256:${match[1].slice(0, 12)}` : image
}
