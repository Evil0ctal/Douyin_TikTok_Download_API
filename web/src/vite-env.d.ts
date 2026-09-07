/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Set only when the console is served from a different origin than the API. */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
