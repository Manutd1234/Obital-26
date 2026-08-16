// Sandboxed code runner worker. JS runs natively with a captured console; Python runs
// via Pyodide (lazy-loaded from CDN the first time). Mirrors the embedding.worker pattern.

type RunRequest = { id: number; lang: 'js' | 'python'; code: string }

type RunResponse =
  | { id: number; type: 'progress'; status: string }
  | { id: number; type: 'result'; ok: boolean; output: string; error: string }

interface PyodideAPI {
  setStdout: (opts: { batched: (s: string) => void }) => void
  setStderr: (opts: { batched: (s: string) => void }) => void
  runPythonAsync: (code: string) => Promise<unknown>
}

const PYODIDE_VERSION = 'v0.26.4'
const PYODIDE_BASE = `https://cdn.jsdelivr.net/pyodide/${PYODIDE_VERSION}/full/`

let pyodidePromise: Promise<PyodideAPI> | null = null

function format(value: unknown): string {
  if (typeof value === 'string') return value
  if (value instanceof Error) return `${value.name}: ${value.message}`
  try {
    const json = JSON.stringify(value, (_k, v) => (typeof v === 'bigint' ? v.toString() : v))
    return json ?? String(value)
  } catch {
    return String(value)
  }
}

function runJs(code: string): { output: string; error: string } {
  const logs: string[] = []
  const capture = (...args: unknown[]) => logs.push(args.map(format).join(' '))
  const sandboxConsole = { log: capture, info: capture, warn: capture, error: capture, debug: capture }
  try {
    // eslint-disable-next-line no-new-func
    const fn = new Function('console', `"use strict";\n${code}`)
    const result = fn(sandboxConsole)
    if (result !== undefined) logs.push(format(result))
    return { output: logs.join('\n'), error: '' }
  } catch (err) {
    return { output: logs.join('\n'), error: format(err) }
  }
}

async function getPyodide(onProgress: (s: string) => void): Promise<PyodideAPI> {
  if (!pyodidePromise) {
    onProgress('Downloading Python runtime (first run only)…')
    pyodidePromise = (async () => {
      const mod = (await import(/* @vite-ignore */ `${PYODIDE_BASE}pyodide.mjs`)) as {
        loadPyodide: (opts: { indexURL: string }) => Promise<PyodideAPI>
      }
      return mod.loadPyodide({ indexURL: PYODIDE_BASE })
    })()
  }
  return pyodidePromise
}

async function runPython(code: string, onProgress: (s: string) => void): Promise<{ output: string; error: string }> {
  const out: string[] = []
  try {
    const pyodide = await getPyodide(onProgress)
    pyodide.setStdout({ batched: (s) => out.push(s) })
    pyodide.setStderr({ batched: (s) => out.push(s) })
    onProgress('Running…')
    await pyodide.runPythonAsync(code)
    return { output: out.join('').replace(/\n$/, ''), error: '' }
  } catch (err) {
    return { output: out.join('').replace(/\n$/, ''), error: format(err) }
  }
}

self.onmessage = async (event: MessageEvent<RunRequest>) => {
  const { id, lang, code } = event.data
  const onProgress = (status: string) =>
    postMessage({ id, type: 'progress', status } satisfies RunResponse)
  try {
    const res = lang === 'python' ? await runPython(code, onProgress) : runJs(code)
    postMessage({ id, type: 'result', ok: !res.error, output: res.output, error: res.error } satisfies RunResponse)
  } catch (err) {
    postMessage({ id, type: 'result', ok: false, output: '', error: format(err) } satisfies RunResponse)
  }
}
