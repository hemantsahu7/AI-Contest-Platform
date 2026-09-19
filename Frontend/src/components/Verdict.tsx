import { Submission } from '../api';

const COLORS: Record<string, string> = {
  ACCEPTED: 'bg-green-100 text-green-800',
  WRONG_ANSWER: 'bg-red-100 text-red-800',
  COMPILATION_ERROR: 'bg-orange-100 text-orange-800',
  RUNTIME_ERROR: 'bg-red-100 text-red-800',
  TIME_LIMIT_EXCEEDED: 'bg-yellow-100 text-yellow-800',
  JUDGE_ERROR: 'bg-purple-100 text-purple-800',
};

export function stateLabel(s: Pick<Submission, 'status' | 'verdict'>): string {
  if (s.status === 'QUEUED') return 'QUEUED';
  if (s.status === 'RUNNING') return 'RUNNING';
  return s.verdict;
}

export function VerdictBadge({ s }: { s: Pick<Submission, 'status' | 'verdict'> }) {
  const label = stateLabel(s);
  const cls = COLORS[label] ?? 'bg-slate-100 text-slate-700 animate-pulse';
  return <span className={`px-2 py-0.5 rounded text-xs font-semibold ${cls}`}>{label.replace(/_/g, ' ')}</span>;
}
