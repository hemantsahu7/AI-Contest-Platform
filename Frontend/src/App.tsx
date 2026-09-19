import { useCallback, useEffect, useState } from 'react';
import { Api, ApiError, getToken, setToken, User } from './api';
import { Auth } from './pages/Auth';
import { Contests } from './pages/Contests';
import { ContestPage } from './pages/ContestPage';
import { ProblemPage } from './pages/ProblemPage';

function useHash(): [string, (h: string) => void] {
  const [hash, setHash] = useState(window.location.hash.slice(1) || '/contests');
  useEffect(() => {
    const on = () => setHash(window.location.hash.slice(1) || '/contests');
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  return [hash, (h) => { window.location.hash = h; }];
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [booting, setBooting] = useState(!!getToken());
  const [hash, go] = useHash();

  useEffect(() => {
    if (!getToken()) return;
    Api.me()
      .then(setUser)
      .catch((e) => { if (e instanceof ApiError && e.status === 401) setToken(null); })
      .finally(() => setBooting(false));
  }, []);

  const logout = useCallback(() => { setToken(null); setUser(null); go('/contests'); }, []);

  if (booting) return <div className="p-8 text-slate-500">Loading...</div>;
  if (!user) return <Auth onAuth={(t, u) => { setToken(t); setUser(u); }} />;

  const parts = hash.split('/').filter(Boolean);
  let page = <Contests user={user} go={go} />;
  if (parts[0] === 'contest' && parts[1] && parts[2] === 'problem' && parts[3]) {
    page = <ProblemPage key={parts[3]} user={user} contestId={parts[1]} problemId={parts[3]} go={go} />;
  } else if (parts[0] === 'contest' && parts[1]) {
    page = <ContestPage key={parts[1]} user={user} contestId={parts[1]} go={go} />;
  }

  return (
    <div className="min-h-screen">
      <header className="bg-slate-900 text-white px-6 py-3 flex items-center justify-between">
        <a href="#/contests" className="font-bold text-lg">Shodh-a-Code</a>
        <div className="text-sm flex items-center gap-4">
          <span>{user.username} <span className="text-slate-400">({user.role})</span></span>
          <button onClick={logout} className="underline">Log out</button>
        </div>
      </header>
      <main className="max-w-7xl mx-auto p-6">{page}</main>
    </div>
  );
}
