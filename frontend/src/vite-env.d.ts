/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_NO_AUTH?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
