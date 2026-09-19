import { useEffect, useState } from 'react';
import { Api, ApiError, Contest, User } from '../api';

export function Contests({ user, go }: { user: User; go: (h: string) => void }) {
  const [contests, setContests] = useState<Contest[] | null>(null);
  const [err, setErr] = useState('');
  const [id, setId] = useState('');

  const load = () => Api.contests().then(setContests).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);

  async function joinById() {
    setErr('');
    const cid = id.trim();
    if (!cid) return;
    try {
      await Api.join(cid);
    } catch (e) {
      // 409 = already joined, which is fine: just open the contest.
      if (!(e instanceof ApiError && e.status === 409)) { setErr((e as Error).message); return; }
    }
    go(`/contest/${cid}`);
  }

  return (
    <div className="space-y-6">
      <section className="bg-white rounded-lg shadow p-5">
        <h2 className="font-semibold mb-2">Join a contest with its Contest ID</h2>
        <div className="flex gap-2">
          <input className="flex-1 border rounded px-3 py-2 font-mono text-sm" placeholder="Contest ID (UUID)" value={id} onChange={(e) => setId(e.target.value)} />
          <button onClick={joinById} className="bg-slate-900 text-white rounded px-4">Join</button>
        </div>
        {err && <div className="text-red-600 text-sm mt-2">{err}</div>}
      </section>
      {user.memberships.length === 0 && user.role !== 'ADMIN' && (
        <div className="bg-amber-50 border border-amber-200 rounded p-3 text-sm">
          You are not a member of any organization yet, so no contests are available. An organization instructor or admin must add you.
        </div>
      )}
      <section>
        <h2 className="font-semibold mb-2">Your organization's contests</h2>
        {!contests ? <div className="text-slate-500">Loading...</div> : contests.length === 0 ? <div className="text-slate-500">No contests available.</div> : (
          <div className="grid gap-3 md:grid-cols-2">
            {contests.map((c) => (
              <a key={c.id} href={`#/contest/${c.id}`} className="bg-white rounded-lg shadow p-4 hover:shadow-md block">
                <div className="flex justify-between"><span className="font-semibold">{c.title}</span><span className="text-xs px-2 py-0.5 rounded bg-slate-100">{c.status}</span></div>
                <p className="text-sm text-slate-500 mt-1">{c.description}</p>
                <p className="text-xs font-mono text-slate-400 mt-2">{c.id}</p>
              </a>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
