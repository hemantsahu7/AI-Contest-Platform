export type Membership = { organizationId: string; role: string };
export type User = { id: string; username: string; email: string; role: string; memberships: Membership[] };
export type Contest = { id: string; title: string; description: string; organizationId: string; startTime: string; endTime: string; status: string };
export type TestCase = { id: string; input: string; expectedOutput: string; isHidden: boolean };
export type Problem = {
  id: string; contestId: string; title: string; description: string; difficulty: string; points: number;
  timeLimitMs: number; memoryLimitMb: number; inputFormat: string; outputFormat: string; testCases: TestCase[];
};
export type Execution = { id: string; status: string; verdict: string; executionTimeMs: number | null; testsPassed: number | null; testsTotal: number | null };
export type Submission = {
  id: string; contestId: string; problemId: string; userId: string; username?: string; status: string; verdict: string; score: number;
  submittedAt: string; completedAt: string | null; problem?: { id: string; title: string; points: number };
  sourceCode?: string; compilerOutput?: string; executions?: Execution[];
};
export type LeaderRow = { rank: number; username: string; score: number; solved: number; totalSolveTimeSeconds: number };
export type Evidence = { id: string; kind: string; title: string; text: string; ref: string; updated?: string; stale?: boolean };
export type Claim = { text: string; kind: 'observation' | 'hypothesis'; evidence: string[] };
export type AiAnswer = {
  answer: string; claims: Claim[]; confidence: string; missing: string[]; needs_clarification: boolean; evidence: Evidence[];
  source: 'llm' | 'fallback' | 'policy'; model?: string; retrieval?: string; degraded?: string; refusal?: string; requestId: string; mode: string; policy: string; timingMs: number;
};

const TOKEN_KEY = 'shodh.token';
export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const setToken = (t: string | null) => (t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY));

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function request<T>(base: string, path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let res: Response;
  try {
    res = await fetch(base + path, { ...init, headers });
  } catch {
    throw new ApiError(0, 'Network error - is the server reachable?');
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = Array.isArray(body.message) ? body.message.join(', ') : body.message ?? body.detail ?? res.statusText;
    throw new ApiError(res.status, msg);
  }
  return body as T;
}

const api = <T,>(path: string, init?: RequestInit) => request<T>('/api', path, init);
const post = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) });

export const Api = {
  login: (email: string, password: string) => api<{ accessToken: string; user: User }>('/auth/login', post({ email, password })),
  register: (b: { username: string; email: string; password: string }) => api<{ accessToken: string; user: User }>('/auth/register', post(b)),
  me: () => api<User>('/auth/me'),
  contests: () => api<Contest[]>('/contests'),
  contest: (id: string) => api<Contest>(`/contests/${id}`),
  join: (id: string) => api<unknown>(`/contests/${id}/join`, { method: 'POST' }),
  participants: (id: string) => api<{ user: { id: string; username: string } }[]>(`/contests/${id}/participants`),
  problems: (id: string) => api<Problem[]>(`/contests/${id}/problems`),
  submit: (cid: string, pid: string, sourceCode: string) => api<{ submissionId: string; status: string }>(`/contests/${cid}/problems/${pid}/submissions`, post({ language: 'cpp', sourceCode })),
  submission: (id: string) => api<Submission>(`/submissions/${id}`),
  submissions: (cid: string) => api<Submission[]>(`/contests/${cid}/submissions`),
  leaderboard: (cid: string) => api<LeaderRow[]>(`/contests/${cid}/leaderboard`),
  ask: (b: { question: string; contestId: string; problemId?: string; submissionId?: string }) => request<AiAnswer>('/ai', '/ask', post(b)),
};

export const isFinal = (s: Submission) => s.status === 'COMPLETED' || s.status === 'INFRASTRUCTURE_ERROR';
