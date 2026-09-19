import { FormEvent, useState } from 'react';
import { Api, User } from '../api';

export function Auth({ onAuth }: { onAuth: (token: string, user: User) => void }) {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [f, setF] = useState({ username: '', email: '', password: '' });
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr(''); setBusy(true);
    try {
      const r = mode === 'login'
        ? await Api.login(f.email, f.password)
        : await Api.register({ username: f.username, email: f.email, password: f.password });
      onAuth(r.accessToken, r.user);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const input = 'w-full border rounded px-3 py-2';
  return (
    <div className="min-h-screen flex items-center justify-center">
      <form onSubmit={submit} className="bg-white shadow rounded-lg p-8 w-96 space-y-3">
        <h1 className="text-2xl font-bold">Shodh-a-Code</h1>
        <p className="text-slate-500 text-sm">{mode === 'login' ? 'Sign in to join a contest' : 'Create a learner account (an instructor adds you to an organization)'}</p>
        {mode === 'register' && <input className={input} placeholder="Username" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} required />}
        <input className={input} type="email" placeholder="Email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} required />
        <input className={input} type="password" placeholder="Password (min 8 chars)" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} required />
        {err && <div className="text-red-600 text-sm">{err}</div>}
        <button disabled={busy} className="w-full bg-slate-900 text-white rounded py-2 disabled:opacity-50">{busy ? '...' : mode === 'login' ? 'Sign in' : 'Register'}</button>
        <button type="button" className="text-sm underline text-slate-600" onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setErr(''); }}>
          {mode === 'login' ? 'Need an account? Register' : 'Have an account? Sign in'}
        </button>
        {mode === 'login' && <p className="text-xs text-slate-400">Demo: learner1@example.com / Password123!</p>}
      </form>
    </div>
  );
}
