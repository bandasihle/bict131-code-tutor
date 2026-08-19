import { useState } from 'react'
import ReactMarkdown from 'react-markdown'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:5000'

// The tutor replies in Markdown — Level 3 in particular comes back with bold
// labels, a numbered list, and a fenced code block. These map each element onto
// the page's existing slate palette instead of pulling in a typography plugin.
//
// Note on `code` + `pre`: react-markdown v10 dropped the `inline` prop, and a
// bare ``` fence carries no language class, so sniffing for `language-*` is not
// a reliable way to tell an inline span from a block. Instead every `code` gets
// the inline chip styling, and `pre` neutralises it on its child — correct for
// both tagged and untagged fences.
const markdownComponents = {
  p: ({ children }) => <p className="mb-3 last:mb-0">{children}</p>,
  strong: ({ children }) => (
    <strong className="font-semibold text-slate-900">{children}</strong>
  ),
  em: ({ children }) => <em className="italic">{children}</em>,
  ul: ({ children }) => (
    <ul className="mb-3 ml-5 list-disc space-y-1 last:mb-0">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="mb-3 ml-5 list-decimal space-y-1 last:mb-0">{children}</ol>
  ),
  li: ({ children }) => <li className="pl-0.5">{children}</li>,
  code: ({ children }) => (
    <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[0.85em] text-slate-900">
      {children}
    </code>
  ),
  pre: ({ children }) => (
    <pre
      className="mb-3 overflow-x-auto rounded-md bg-slate-900 p-3 text-[0.85em] leading-relaxed last:mb-0
                 [&>code]:bg-transparent [&>code]:p-0 [&>code]:text-slate-100"
    >
      {children}
    </pre>
  ),
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="underline underline-offset-2 hover:text-slate-950"
    >
      {children}
    </a>
  ),
}

export default function App() {
  const [code, setCode] = useState('')
  const [issue, setIssue] = useState('')
  const [reply, setReply] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  // How many times the student has asked about this bug. The server decides what
  // this means — the UI just reports it. See prompts.py.
  const [attempt, setAttempt] = useState(0)

  const canSubmit = code.trim() && issue.trim() && !loading

  async function askTutor(event) {
    event.preventDefault()
    if (!canSubmit) return

    setLoading(true)
    setError('')
    setReply('')

    try {
      const response = await fetch(`${API_URL}/tutor`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, issue, attempt }),
      })

      const data = await response.json()

      if (!response.ok) {
        setError(data.error || `Request failed (${response.status}).`)
        return
      }

      setReply(data.reply)
      setAttempt((n) => n + 1)
    } catch {
      setError(
        `Could not reach the backend at ${API_URL}. Is the Flask server running?`,
      )
    } finally {
      setLoading(false)
    }
  }

  function startOver() {
    setCode('')
    setIssue('')
    setReply('')
    setError('')
    setAttempt(0)
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <div className="mx-auto max-w-3xl px-6 py-10">
        <header className="mb-8">
          <h1 className="text-2xl font-semibold tracking-tight">
            BICT131 Code Tutor
          </h1>
          <p className="mt-1 text-sm text-slate-600">
            Paste your code and describe what&apos;s going wrong. The tutor asks a
            question — it won&apos;t just hand you the fix.
          </p>
        </header>

        <form onSubmit={askTutor} className="space-y-5">
          <div>
            <label
              htmlFor="code"
              className="mb-1.5 block text-sm font-medium text-slate-700"
            >
              Your code
            </label>
            <textarea
              id="code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              rows={12}
              spellCheck={false}
              placeholder="Paste your code here..."
              className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-sm
                         shadow-sm outline-none placeholder:text-slate-400
                         focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
            />
          </div>

          <div>
            <label
              htmlFor="issue"
              className="mb-1.5 block text-sm font-medium text-slate-700"
            >
              What&apos;s going wrong?
            </label>
            <textarea
              id="issue"
              value={issue}
              onChange={(e) => setIssue(e.target.value)}
              rows={3}
              placeholder="e.g. it prints one number too few"
              className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm
                         shadow-sm outline-none placeholder:text-slate-400
                         focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
            />
          </div>

          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={!canSubmit}
              className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white shadow-sm
                         transition hover:bg-slate-700
                         disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {loading ? 'Thinking…' : 'Ask the Tutor'}
            </button>

            {attempt > 0 && (
              <>
                <span className="text-sm text-slate-500">
                  Attempt {attempt}
                </span>
                <button
                  type="button"
                  onClick={startOver}
                  className="text-sm text-slate-500 underline underline-offset-2 hover:text-slate-800"
                >
                  Start over
                </button>
              </>
            )}
          </div>
        </form>

        <section className="mt-8" aria-live="polite">
          {error && (
            <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
              {error}
            </div>
          )}

          {reply && (
            <div className="rounded-md border border-slate-200 bg-white px-5 py-4 shadow-sm">
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                Tutor
              </h2>
              <div className="text-sm leading-relaxed text-slate-800">
                <ReactMarkdown components={markdownComponents}>
                  {reply}
                </ReactMarkdown>
              </div>
            </div>
          )}

          {!error && !reply && !loading && (
            <p className="text-sm text-slate-400">
              The tutor&apos;s reply will appear here.
            </p>
          )}
        </section>
      </div>
    </div>
  )
}
