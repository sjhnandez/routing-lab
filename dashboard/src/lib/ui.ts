/** Shared class strings for the design language. */

/** Repository this dashboard reports on. */
export const GITHUB_URL = 'https://github.com/sjhnandez/routing-lab'

/** Base card surface: subtle border, soft raised background. */
export const card = 'rounded-2xl border border-line bg-surface/60'

/** Hover treatment for cards: bordering glow. */
export const cardGlow =
  'transition duration-300 hover:border-accent/40 hover:shadow-[0_18px_50px_-18px_rgba(79,140,255,0.45)]'

/** Primary action button (accent gradient). */
export const buttonSolid =
  'inline-flex items-center justify-center gap-2 rounded-full bg-linear-to-r from-accent to-accent2 font-semibold text-white shadow-lg shadow-accent/20 transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:brightness-100'

/** Secondary action button (quiet border). */
export const buttonGhost =
  'inline-flex items-center justify-center gap-2 rounded-full border border-line text-sm font-medium text-muted transition hover:border-accent/60 hover:text-ink disabled:cursor-not-allowed disabled:opacity-60'

/** Small monospace pill; set the text colors at the usage site. */
export const chip =
  'inline-flex items-center gap-1.5 rounded-full border border-line px-2.5 py-0.5 font-mono text-[11px]'

/** Spinner for dark/gradient surfaces; add a size. */
export const spinnerOnGradient =
  'animate-spin rounded-full border-2 border-white/30 border-t-white motion-reduce:animate-none'

/** Spinner for dark surfaces; add a size. */
export const spinnerAccent =
  'animate-spin rounded-full border-2 border-line border-t-accent motion-reduce:animate-none'
