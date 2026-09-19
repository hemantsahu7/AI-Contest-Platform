import { useState } from 'react';
import { AiAnswer, Api } from '../api';

type Turn = { q: string; a?: AiAnswer; error?: string; pending?: boolean };

const SOURCE_LABEL: Record<string, string> = {
  llm: 'Gemini',
  fallback: 'Evidence-only (no model)',
  policy: 'Policy refusal',
};

export function Assistant({ contestId, problemId, submissionId, staff }: { contestId: string; problemId?: string; submissionId?: string; staff: boolean }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);

  async function ask(question: string) {
    if (!question.trim() || busy) return;
    setBusy(true);
    setQ('');
    setTurns((t) => [...t, { q: question, pending: true }]);
    try {
      const a = await Api.ask({ question, contestId, problemId, submissionId });
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { q: question, a } : x)));
    } catch (e) {
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { q: question, error: (e as Error).message } : x)));
    } finally {
      setBusy(false);
    }
  }

  const quick = staff
    ? ['Summarize verdicts and separate judge errors from code errors', 'Which learners may share a prerequisite gap?']
    : ['Explain this problem', 'Give me a hint', 'Why did my latest submission get this verdict?', 'What should I study to solve this?'];

  return (
    <div className="bg-white rounded-lg shadow flex flex-col h-[42rem]">
      <div className="p-3 border-b">
        <div className="font-semibold">AI assistant</div>
        <div className="text-xs text-slate-500">Read-only. Explains recorded judge evidence; never decides verdicts or scores.</div>
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-4 text-sm">
        {turns.length === 0 && <div className="text-slate-400">Ask about the problem, a hint, or your latest submission.</div>}
        {turns.map((t, i) => (
          <div key={i} className="space-y-1">
            <div className="bg-slate-100 rounded p-2 ml-6">{t.q}</div>
            {t.pending && <div className="text-slate-400 animate-pulse">Gathering evidence...</div>}
            {t.error && <div className="text-red-600 bg-red-50 rounded p-2">{t.error} <button className="underline" onClick={() => ask(t.q)}>Retry</button></div>}
            {t.a && <Answer a={t.a} />}
          </div>
        ))}
      </div>
      <div className="p-3 border-t space-y-2">
        <div className="flex flex-wrap gap-1">
          {quick.map((s) => <button key={s} disabled={busy} onClick={() => ask(s)} className="text-xs border rounded px-2 py-1 hover:bg-slate-50 disabled:opacity-50">{s}</button>)}
        </div>
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); ask(q); }}>
          <input className="flex-1 border rounded px-3 py-2" placeholder="Ask a question..." value={q} onChange={(e) => setQ(e.target.value)} maxLength={1000} />
          <button disabled={busy} className="bg-slate-900 text-white rounded px-4 disabled:opacity-50">Ask</button>
        </form>
      </div>
    </div>
  );
}

function Answer({ a }: { a: AiAnswer }) {
  const [open, setOpen] = useState(false);
  const [showSteps, setShowSteps] = useState(false);
  const conf = { high: 'text-green-700', medium: 'text-amber-700', low: 'text-red-700' }[a.confidence] ?? '';
  return (
    <div className="border rounded p-2 space-y-2">
      <div className="flex flex-wrap gap-2 text-xs">
        <span className="bg-slate-100 rounded px-2 py-0.5">{SOURCE_LABEL[a.source]}{a.model ? ` - ${a.model}` : ''}</span>
        {a.retrieval && <span className="bg-slate-100 rounded px-2 py-0.5">Retrieval: {a.retrieval}</span>}
        <span className={`rounded px-2 py-0.5 bg-slate-50 ${conf}`}>Confidence: {a.confidence}</span>
        <span className="text-slate-400">{a.timingMs} ms - req {a.requestId}</span>
      </div>
      {a.degraded && <div className="text-xs bg-amber-50 text-amber-800 rounded p-1.5">{a.degraded}</div>}
      {a.claims.length > 0 ? (
        <ul className="space-y-1">
          {a.claims.map((c, i) => (
            <li key={i}>
              <span className={`text-[10px] font-semibold mr-1 px-1 rounded ${c.kind === 'observation' ? 'bg-blue-100 text-blue-800' : 'bg-amber-100 text-amber-800'}`}>{c.kind === 'observation' ? 'OBSERVED' : 'HYPOTHESIS'}</span>
              {c.text}{' '}
              {c.evidence.map((id) => <span key={id} className="text-[10px] font-mono bg-slate-100 rounded px-1 mr-0.5">{id}</span>)}
            </li>
          ))}
        </ul>
      ) : <div className="whitespace-pre-wrap">{a.answer}</div>}
      {a.needs_clarification && <div className="text-xs text-blue-700">The assistant needs clarification to answer this reliably.</div>}
      {a.missing.length > 0 && (
        <div className="text-xs text-slate-600"><b>Not established:</b><ul className="list-disc ml-4">{a.missing.map((m, i) => <li key={i}>{m}</li>)}</ul></div>
      )}
      {a.evidence.length > 0 && (
        <div>
          <button className="text-xs underline text-slate-600" onClick={() => setOpen(!open)}>{open ? 'Hide' : 'Show'} evidence ({a.evidence.length})</button>
          {open && (
            <ul className="mt-1 space-y-1">
              {a.evidence.map((e) => (
                <li key={e.id} className="text-xs bg-slate-50 rounded p-1.5">
                  <span className="font-mono font-semibold">{e.id}</span> {e.title} <span className="text-slate-400">({e.kind}{e.updated ? `, updated ${e.updated.slice(0, 10)}` : ''})</span>
                  {e.stale && <span className="ml-1 text-amber-700 font-semibold">may be outdated</span>}
                  {e.via && <span className="ml-1 text-[10px] bg-indigo-50 text-indigo-700 rounded px-1">found by: {e.via}</span>}
                  {e.kind === 'source'
                    ? <pre className="mt-1 bg-slate-900 text-slate-100 rounded p-2 overflow-x-auto max-h-64">{e.text}</pre>
                    : <div className="text-slate-600 whitespace-pre-wrap break-words">{e.text.slice(0, 500)}</div>}
                  <div className="font-mono text-slate-400">{e.ref}</div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {a.agent && a.agent.steps.length > 0 && (
        <div>
          <button className="text-xs underline text-slate-600" onClick={() => setShowSteps(!showSteps)}>
            {showSteps ? 'Hide' : 'Show'} tool steps ({a.agent.steps.length}/{a.agent.maxSteps}, read-only)
          </button>
          {showSteps && (
            <ol className="mt-1 space-y-1">
              {a.agent.steps.map((s) => (
                <li key={s.step} className="text-xs bg-slate-50 rounded p-1.5">
                  <span className="font-mono font-semibold">{s.step}. {s.tool}</span>{' '}
                  <span className={s.status === 'ok' ? 'text-green-700' : 'text-amber-700'}>{s.status}</span>{' '}
                  <span className="text-slate-400">{s.ms} ms</span>
                  <div className="text-slate-600">{s.why} - {s.summary}</div>
                  {s.evidence.length > 0 && <div className="font-mono text-slate-400">evidence: {s.evidence.join(', ')}</div>}
                </li>
              ))}
              <li className="text-[10px] text-slate-400">Stopped: {a.agent.stopReason} (planner: {a.agent.planner})</li>
            </ol>
          )}
        </div>
      )}
      <div className="text-[10px] text-slate-400">Policy: {a.policy}</div>
    </div>
  );
}
