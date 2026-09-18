import { buildLeaderboard } from './leaderboard.util';

describe('buildLeaderboard', () => {
  const start = new Date('2026-01-01T00:00:00.000Z');
  const participants = [
    { userId: 'u2', username: 'bob' },
    { userId: 'u1', username: 'alice' },
  ];

  it('scores each accepted problem once and ranks by score then time then userId', () => {
    const rows = buildLeaderboard(
      [
        {
          userId: 'u1',
          username: 'alice',
          problemId: 'p1',
          points: 100,
          acceptedAt: new Date('2026-01-01T00:10:00.000Z'),
        },
        {
          userId: 'u1',
          username: 'alice',
          problemId: 'p1',
          points: 100,
          acceptedAt: new Date('2026-01-01T00:20:00.000Z'),
        },
        {
          userId: 'u2',
          username: 'bob',
          problemId: 'p1',
          points: 100,
          acceptedAt: new Date('2026-01-01T00:05:00.000Z'),
        },
        {
          userId: 'u2',
          username: 'bob',
          problemId: 'p2',
          points: 200,
          acceptedAt: new Date('2026-01-01T00:06:00.000Z'),
        },
      ],
      start,
      participants,
    );

    expect(rows[0]).toMatchObject({ username: 'bob', score: 300, solved: 2, rank: 1 });
    expect(rows[1]).toMatchObject({ username: 'alice', score: 100, solved: 1, rank: 2 });
    expect(rows[1].totalSolveTimeSeconds).toBe(600);
  });

  it('uses userId as a deterministic tie-breaker', () => {
    const rows = buildLeaderboard([], start, participants);
    expect(rows.map((r) => r.userId)).toEqual(['u1', 'u2']);
  });
});
