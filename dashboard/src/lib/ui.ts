/** Shared class strings for the design language: square, hairlined, flat. */

/** Repository this dashboard reports on. */
export const GITHUB_URL = 'https://github.com/sjhnandez/routing-lab'

/** Base panel: a hairline frame on white. No radius, shadow or hover lift. */
export const card = 'border border-line bg-surface'

/** Primary action: solid ink block. */
export const buttonSolid =
  'inline-flex items-center justify-center gap-2 bg-ink text-bg transition-colors hover:bg-[#3a393d] disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-ink'

/** Secondary action: hairline frame. */
export const buttonGhost =
  'inline-flex items-center justify-center gap-2 border border-line text-sm text-muted transition-colors hover:border-ink hover:text-ink disabled:cursor-not-allowed disabled:opacity-50'

/** Small monospace readout tag; set the text colors at the usage site. */
export const chip =
  'inline-flex items-center gap-1.5 border border-line px-2 py-0.5 font-mono text-[11px]'

/** Spinner for ink-filled buttons; add a size. */
export const spinnerOnGradient =
  'animate-spin rounded-full border-2 border-white/30 border-t-white motion-reduce:animate-none'

/** Spinner for light surfaces; add a size. */
export const spinnerAccent =
  'animate-spin rounded-full border-2 border-line border-t-ink motion-reduce:animate-none'
