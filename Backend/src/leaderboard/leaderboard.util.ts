export type SolveEvent = {
  userId: string;
  username: string;
  problemId: string;
  points: number;
  acceptedAt: Date;
};

export type RankedRow = {
  rank: number;
  userId: string;
  username: string;
  score: number;
  solved: number;
  totalSolveTimeSeconds: number;
};

export function buildLeaderboard(
  events: SolveEvent[],
  contestStart: Date,
  participants: { userId: string; username: string }[],
): RankedRow[] {
  const firstAccept = new Map<string, Map<string, SolveEvent>>();
  for (const event of events) {
    const byProblem = firstAccept.get(event.userId) ?? new Map<string, SolveEvent>();
    const existing = byProblem.get(event.problemId);
    if (!existing || event.acceptedAt < existing.acceptedAt) {
      byProblem.set(event.problemId, event);
    }
    firstAccept.set(event.userId, byProblem);
  }

  const rows = participants.map((p) => {
    const solved = [...(firstAccept.get(p.userId)?.values() ?? [])];
    const score = solved.reduce((sum, s) => sum + s.points, 0);
    const totalSolveTimeSeconds = solved.reduce((sum, s) => {
      const delta = Math.max(0, Math.floor((s.acceptedAt.getTime() - contestStart.getTime()) / 1000));
      return sum + delta;
    }, 0);
    return {
      userId: p.userId,
      username: p.username,
      score,
      solved: solved.length,
      totalSolveTimeSeconds,
    };
  });

  rows.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    if (a.totalSolveTimeSeconds !== b.totalSolveTimeSeconds) {
      return a.totalSolveTimeSeconds - b.totalSolveTimeSeconds;
    }
    return a.userId.localeCompare(b.userId);
  });

  return rows.map((row, index) => ({ rank: index + 1, ...row }));
}
