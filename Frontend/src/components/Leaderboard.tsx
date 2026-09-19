import { useEffect, useState } from 'react';
import { Api, LeaderRow } from '../api';

export function Leaderboard({ contestId, me, refreshKey }: { contestId: string; me: string; refreshKey?: number }) {
  const [rows, setRows] = useState<LeaderRow[] | null>(null);
  const [stale, setStale] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () => Api.leaderboard(contestId).then((r) => { if (alive) { setRows(r); setStale(false); } }).catch(() => alive && setStale(true));
    load();
    const t = setInterval(load, 5000);
    return () => { alive = false; clearInterval(t); };
  }, [contestId, refreshKey]);

  if (!rows) return <div className="text-slate-500">Loading...</div>;
  return (
    <div>
      {stale && <div className="text-xs text-amber-600 mb-1">Connection issue - showing last known ranking, retrying...</div>}
      <table className="w-full text-sm bg-white rounded shadow">
        <thead><tr className="text-left text-slate-500 border-b"><th className="p-2">#</th><th>User</th><th>Score</th><th>Solved</th><th>Solve time (s)</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.username} className={`border-b last:border-0 ${r.username === me ? 'bg-yellow-50 font-semibold' : ''}`}>
              <td className="p-2">{r.rank}</td><td>{r.username}</td><td>{r.score}</td><td>{r.solved}</td><td>{r.totalSolveTimeSeconds}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-slate-400 mt-1">Updates every 5 s. Ranked by score, then total solve time.</p>
    </div>
  );
}
