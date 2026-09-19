import { useEffect, useState } from 'react';
import { Api, ApiError, Contest, Problem, Submission, User } from '../api';
import { Assistant } from '../components/Assistant';
import { Leaderboard } from '../components/Leaderboard';
import { VerdictBadge } from '../components/Verdict';

export function ContestPage({ user, contestId, go }: { user: User; contestId: string; go: (h: string) => void }) {
  const [contest, setContest] = useState<Contest | null>(null);
  const [problems, setProblems] = useState<Problem[]>([]);
  const [joined, setJoined] = useState<boolean | null>(null);
  const [tab, setTab] = useState<'problems' | 'leaderboard' | 'submissions'>('problems');
  const [subs, setSubs] = useState<Submission[]>([]);
  const [err, setErr] = useState('');

  const isStaff = user.role === 'ADMIN' || user.memberships.some((m) => m.organizationId === contest?.organizationId && m.role !== 'LEARNER');

  async function load() {
    try {
      const c = await Api.contest(contestId);
      setContest(c);
      const [ps, parts] = await Promise.all([Api.problems(contestId), Api.participants(contestId)]);
      setProblems(ps);
      setJoined(parts.some((p) => p.user.id === user.id));
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  useEffect(() => { load(); }, [contestId]);
  useEffect(() => {
    if (tab !== 'submissions') return;
    const f = () => Api.submissions(contestId).then(setSubs).catch(() => undefined);
    f();
    const t = setInterval(f, 4000);
    return () => clearInterval(t);
  }, [tab, contestId]);

  async function join() {
    try { await Api.join(contestId); } catch (e) { if (!(e instanceof ApiError && e.status === 409)) { setErr((e as Error).message); return; } }
    setJoined(true);
  }

  if (err && !contest) return <div className="text-red-600">{err} <a className="underline" href="#/contests">Back</a></div>;
  if (!contest) return <div className="text-slate-500">Loading...</div>;

  const tabBtn = (t: typeof tab, label: string) => (
    <button onClick={() => setTab(t)} className={`px-3 py-1.5 rounded ${tab === t ? 'bg-slate-900 text-white' : 'bg-white shadow'}`}>{label}</button>
  );

  return (
    <div className="grid lg:grid-cols-3 gap-6">
      <div className="lg:col-span-2 space-y-4">
        <div>
          <a href="#/contests" className="text-sm text-slate-500 underline">All contests</a>
          <h1 className="text-2xl font-bold">{contest.title} <span className="text-xs align-middle px-2 py-0.5 rounded bg-slate-200">{contest.status}</span></h1>
          <p className="text-slate-600">{contest.description}</p>
          <p className="text-xs text-slate-400 font-mono">Contest ID: {contest.id}</p>
        </div>
        {joined === false && !isStaff && (
          <div className="bg-amber-50 border border-amber-200 rounded p-3 flex items-center justify-between">
            <span>You haven't joined this contest yet.</span>
            <button onClick={join} className="bg-slate-900 text-white rounded px-3 py-1">Join contest</button>
          </div>
        )}
        {err && <div className="text-red-600 text-sm">{err}</div>}
        <div className="flex gap-2">{tabBtn('problems', 'Problems')}{tabBtn('leaderboard', 'Leaderboard')}{tabBtn('submissions', isStaff ? 'All submissions' : 'My submissions')}</div>
        {tab === 'problems' && (
          <div className="space-y-2">
            {problems.map((p) => (
              <a key={p.id} href={`#/contest/${contestId}/problem/${p.id}`} className="bg-white rounded-lg shadow p-4 flex justify-between hover:shadow-md">
                <span className="font-semibold">{p.title}</span>
                <span className="text-sm text-slate-500">{p.difficulty} - {p.points} pts</span>
              </a>
            ))}
            {problems.length === 0 && <div className="text-slate-500">No problems.</div>}
          </div>
        )}
        {tab === 'leaderboard' && <Leaderboard contestId={contestId} me={user.username} />}
        {tab === 'submissions' && (
          <table className="w-full text-sm bg-white rounded shadow">
            <thead><tr className="text-left text-slate-500 border-b"><th className="p-2">When</th>{isStaff && <th>User</th>}<th>Problem</th><th>Verdict</th><th>Score</th></tr></thead>
            <tbody>
              {subs.map((s) => (
                <tr key={s.id} className="border-b last:border-0 cursor-pointer hover:bg-slate-50" onClick={() => go(`/contest/${contestId}/problem/${s.problemId}`)}>
                  <td className="p-2">{new Date(s.submittedAt).toLocaleString()}</td>{isStaff && <td>{s.username}</td>}
                  <td>{s.problem?.title}</td><td><VerdictBadge s={s} /></td><td>{s.score}</td>
                </tr>
              ))}
              {subs.length === 0 && <tr><td className="p-3 text-slate-400" colSpan={5}>No submissions yet.</td></tr>}
            </tbody>
          </table>
        )}
      </div>
      <Assistant contestId={contestId} staff={isStaff} />
    </div>
  );
}
