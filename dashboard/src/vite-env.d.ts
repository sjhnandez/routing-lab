/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Override for the lab API base URL; defaults to same-origin `/api`. */
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
