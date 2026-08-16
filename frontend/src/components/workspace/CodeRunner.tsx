import { useState } from 'react'
import CodeMirror from '@uiw/react-codemirror'
import { javascript } from '@codemirror/lang-javascript'
import { python } from '@codemirror/lang-python'
import { oneDark } from '@codemirror/theme-one-dark'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import { useUIStore } from '../../stores/uiStore'
import { runCode, type CodeLang } from '../../lib/codeRunnerClient'

const DEFAULTS: Record<CodeLang, string> = {
  js: `// JavaScript runs instantly in a sandboxed worker.\nconsole.log('Hello, Obital!')\nconsole.log([1, 2, 3].map((n) => n * 2))`,
  python: `# Python runs via Pyodide (downloads once on first run).\nprint("Hello, Obital!")\nprint(sum(range(10)))`,
}

export function CodeRunner() {
  const code = useWorkspaceStore((s) => s.data.code)
  const setCode = useWorkspaceStore((s) => s.setCode)
  const theme = useUIStore((s) => s.theme)

  const [lang, setLang] = useState<CodeLang>('js')
  const [running, setRunning] = useState(false)
  const [status, setStatus] = useState('')
  const [result, setResult] = useState<{ output: string; error: string } | null>(null)

  const value = code[lang] !== '' ? code[lang] : DEFAULTS[lang]

  const handleRun = async () => {
    setRunning(true)
    setStatus(lang === 'python' ? 'Preparing Python…' : 'Running…')
    setResult(null)
    try {
      const res = await runCode(lang, value, setStatus)
      setResult({ output: res.output, error: res.error })
    } catch (err) {
      setResult({ output: '', error: err instanceof Error ? err.message : String(err) })
    } finally {
      setRunning(false)
      setStatus('')
    }
  }

  return (
    <div className="flex h-full flex-col gap-3 p-4">
      {/* Toolbar */}
      <div className="flex flex-shrink-0 items-center gap-2">
        <div className="flex gap-1 rounded-lg bg-surface-light p-1">
          {(['js', 'python'] as CodeLang[]).map((l) => (
            <button
              key={l}
              onClick={() => setLang(l)}
              className={`rounded-md px-3 py-1 font-mono text-xs transition-colors ${
                lang === l
                  ? 'bg-surface-card text-text-primary shadow-sm'
                  : 'text-accent-muted hover:text-text-primary'
              }`}
            >
              {l === 'js' ? 'JavaScript' : 'Python'}
            </button>
          ))}
        </div>

        <div className="flex-1" />

        {running && status && (
          <span className="font-mono text-[11px] text-accent-muted">{status}</span>
        )}
        <button
          onClick={handleRun}
          disabled={running}
          className="flex items-center gap-1.5 rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-surface transition-colors hover:bg-accent-hover disabled:opacity-50"
        >
          <span className="material-symbols-outlined text-base">play_arrow</span>
          {running ? 'Running…' : 'Run'}
        </button>
      </div>

      {/* Editor */}
      <div className="min-h-0 flex-1 overflow-hidden rounded-xl border border-border/10">
        <CodeMirror
          value={value}
          height="100%"
          className="h-full text-sm"
          theme={theme === 'dark' ? oneDark : 'light'}
          extensions={lang === 'python' ? [python()] : [javascript()]}
          onChange={(val) => setCode(lang, val)}
          basicSetup={{ lineNumbers: true, highlightActiveLine: true, bracketMatching: true }}
        />
      </div>

      {/* Output */}
      <div className="flex h-[34%] min-h-[120px] flex-shrink-0 flex-col overflow-hidden rounded-xl border border-border/10 bg-surface-light">
        <div className="flex items-center justify-between border-b border-border/10 px-3 py-1.5">
          <span className="font-mono text-[10px] uppercase tracking-wider text-accent-muted">Output</span>
          {result && (
            <button
              onClick={() => setResult(null)}
              className="font-mono text-[10px] text-accent-muted hover:text-text-primary"
            >
              clear
            </button>
          )}
        </div>
        <div className="custom-scrollbar flex-1 overflow-auto p-3 font-mono text-xs leading-relaxed">
          {!result && !running && (
            <span className="text-accent-muted/50">Press Run to execute your code.</span>
          )}
          {running && <span className="text-accent-muted">{status || 'Running…'}</span>}
          {result?.output && <pre className="whitespace-pre-wrap text-text-primary">{result.output}</pre>}
          {result?.error && (
            <pre className="mt-1 whitespace-pre-wrap text-danger">{result.error}</pre>
          )}
          {result && !result.output && !result.error && (
            <span className="text-accent-muted/50">(no output)</span>
          )}
        </div>
      </div>
    </div>
  )
}
