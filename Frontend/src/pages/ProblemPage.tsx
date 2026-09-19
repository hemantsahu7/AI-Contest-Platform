import { useCallback, useEffect, useRef, useState } from 'react';
import { Api, isFinal, Problem, Submission, User } from '../api';
import { Assistant } from '../components/Assistant';
import { Leaderboard } from '../components/Leaderboard';
import { VerdictBadge } from '../components/Verdict';

const TEMPLATE = `#include <iostream>
using namespace std;

int main() {
  // read input, print the answer
  return 0;
}
`;

const STEPS = ['QUEUED', 'RUNNING', 'DONE'];

export function ProblemPage({ user, contestId, problemId }: { user: User; contestId: string; problemId: string; go: (h: string) => void }) {
  const draftKey = `shodh.draft.${problemId}`;
  const [problem, setProblem] = useState<Problem | null>(null);
  const [code, setCode] = useState(() => localStorage.getItem(draftKey) ?? TEMPLATE);
  const [history, setHistory] = useState<Submission[]>([]);
  const [active, setActive] = useState<Submission | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [netIssue, setNetIssue] = useState(false);
  const [lbKey, setLbKey] = useState(0);
  const timer = useRef<number | undefined>(undefined);

  const isStaff = user.role === 'ADMIN' || user.memberships.some((m) => m.role !== 'LEARNER');

  const refreshHistory = useCallback(async () => {
    const all = await Api.submissions(contestId);
    const mine = all.filter((s) => s.problemId === problemId && (isStaff ? s.userId === user.id : true));
    setHistory(mine);
    return mine;
  }, [contestId, problemId]);

  // Poll one submission until it reaches a final state; survives transient network errors.
  const track = useCallback((id: string) => {
    window.clearTimeout(timer.current);
    const tick = async () => {
      try {
        const s = await Api.submission(id);
        setActive(s); setNetIssue(false);
        if (isFinal(s)) { await refreshHistory().catch(() => undefined); setLbKey((k) => k + 1); return; }
      } catch { setNetIssue(true); }
      timer.current = window.setTimeout(tick, 1500);
    };
    tick();
  }, [refreshHistory]);

  useEffect(() => {
    Api.problems(contestId).then((ps) => setProblem(ps.find((p) => p.id === problemId) ?? null)).catch((e) => setErr(e.message));
    // Resume after a refresh: pick up the latest submission and keep polling if it is still in flight.
    refreshHistory().then((mine) => {
      const latest = mine[0];
      if (latest) { setActive(latest); if (!isFinal(latest)) track(latest.id); else Api.submission(latest.id).then(setActive).catch(() => undefined); }
    }).catch(() => undefined);
    return () => window.clearTimeout(timer.current);
  }, [contestId, problemId]);

  useEffect(() => { localStorage.setItem(draftKey, code); }, [code]);

  async function submit() {
    setErr(''); setBusy(true);
    try {
      const r = await Api.submit(contestId, problemId, code);
      setActive({ id: r.submissionId, contestId, problemId, userId: user.id, status: 'QUEUED', verdict: 'PENDING', score: 0, submittedAt: new Date().toISOString(), completedAt: null });
      track(r.submissionId);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!problem) return <div className="text-slate-500">{err || 'Loading...'}</div>;
  const step = !active ? -1 : active.status === 'QUEUED' ? 0 : active.status === 'RUNNING' ? 1 : 2;
  const ex = active?.executions?.[0];

  return (
    <div className="grid lg:grid-cols-3 gap-6">
      <div className="lg:col-span-2 space-y-4">
        <a href={`#/contest/${contestId}`} className="text-sm text-slate-500 underline">Back to contest</a>
        <section className="bg-white rounded-lg shadow p-5 space-y-2">
          <h1 className="text-xl font-bold">{problem.title} <span className="text-sm font-normal text-slate-500">{problem.difficulty} - {problem.points} pts - {problem.timeLimitMs} ms - {problem.memoryLimitMb} MB</span></h1>
          <p>{problem.description}</p>
          <p className="text-sm"><b>Input:</b> {problem.inputFormat}</p>
          <p className="text-sm"><b>Output:</b> {problem.outputFormat}</p>
          {problem.testCases.map((t, i) => (
            <div key={t.id} className="text-sm font-mono bg-slate-50 rounded p-2">
              <div className="text-xs text-slate-400">Example {i + 1}</div>
              <div>in: {t.input.trim()}</div><div>out: {t.expectedOutput.trim()}</div>
            </div>
          ))}
        </section>

        <section className="bg-white rounded-lg shadow p-4 space-y-2">
          <div className="flex justify-between items-center">
            <span className="font-semibold">C++ solution</span>
            <button className="text-xs underline text-slate-500" onClick={() => setCode(TEMPLATE)}>Reset template</button>
          </div>
          <textarea
            value={code}
            onChange={(e) => setCode(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Tab') {
                e.preventDefault();
                const t = e.currentTarget; const s = t.selectionStart;
                setCode(code.slice(0, s) + '  ' + code.slice(t.selectionEnd));
                requestAnimationFrame(() => { t.selectionStart = t.selectionEnd = s + 2; });
              }
            }}
            spellCheck={false}
            className="w-full h-72 font-mono text-sm border rounded p-3 bg-slate-950 text-slate-100"
          />
          {err && <div className="text-red-600 text-sm">{err}</div>}
          <button onClick={submit} disabled={busy || (!!active && !isFinal(active))} className="bg-slate-900 text-white rounded px-5 py-2 disabled:opacity-50">
            {busy ? 'Submitting...' : active && !isFinal(active) ? 'Judging...' : 'Submit'}
          </button>
        </section>

        {active && (
          <section className="bg-white rounded-lg shadow p-4 space-y-3">
            <div className="flex items-center gap-2 text-sm">
              {STEPS.map((s, i) => (
                <span key={s} className={`px-2 py-1 rounded ${i <= step ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-400'} ${i === step && step < 2 ? 'animate-pulse' : ''}`}>{s === 'DONE' ? 'Verdict' : s === 'QUEUED' ? 'Queued' : 'Running'}</span>
              ))}
              {netIssue && <span className="text-amber-600 text-xs">connection issue, retrying...</span>}
            </div>
            <div className="flex items-center gap-3">
              <VerdictBadge s={active} /> <span className="text-sm">Score: {active.score}</span>
              {ex && ex.testsTotal != null && isFinal(active) && <span className="text-sm text-slate-500">{ex.testsPassed}/{ex.testsTotal} tests passed - {ex.executionTimeMs} ms</span>}
              <span className="text-xs text-slate-400 font-mono">{active.id.slice(0, 8)}</span>
            </div>
            {active.verdict === 'JUDGE_ERROR' && <div className="text-sm bg-purple-50 text-purple-800 rounded p-2">The judge had an infrastructure problem. This is not a mistake in your code - please resubmit.</div>}
            {active.compilerOutput && <pre className="text-xs bg-slate-950 text-orange-300 rounded p-3 overflow-x-auto whitespace-pre-wrap">{active.compilerOutput}</pre>}
          </section>
        )}

        <section>
          <h3 className="font-semibold mb-1">Submission history</h3>
          <table className="w-full text-sm bg-white rounded shadow">
            <tbody>
              {history.map((s) => (
                <tr key={s.id} className="border-b last:border-0 cursor-pointer hover:bg-slate-50" onClick={() => Api.submission(s.id).then((d) => { setActive(d); if (!isFinal(d)) track(d.id); })}>
                  <td className="p-2">{new Date(s.submittedAt).toLocaleString()}</td><td><VerdictBadge s={s} /></td><td>{s.score} pts</td>
                </tr>
              ))}
              {history.length === 0 && <tr><td className="p-3 text-slate-400">No submissions yet.</td></tr>}
            </tbody>
          </table>
        </section>
        <section><h3 className="font-semibold mb-1">Leaderboard</h3><Leaderboard contestId={contestId} me={user.username} refreshKey={lbKey} /></section>
      </div>
      <Assistant contestId={contestId} problemId={problemId} submissionId={active && isFinal(active) ? active.id : undefined} staff={isStaff} />
    </div>
  );
}
