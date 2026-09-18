import { effectiveContestStatus } from './contest-status';

describe('effectiveContestStatus', () => {
  const base = {
    startTime: new Date('2026-01-02T00:00:00.000Z'),
    endTime: new Date('2026-01-03T00:00:00.000Z'),
  };

  it('keeps draft contests in DRAFT regardless of time', () => {
    expect(
      effectiveContestStatus(
        { ...base, status: 'DRAFT' },
        new Date('2026-01-02T12:00:00.000Z'),
      ),
    ).toBe('DRAFT');
  });

  it('derives upcoming, running, and ended from timestamps', () => {
    expect(
      effectiveContestStatus(
        { ...base, status: 'UPCOMING' },
        new Date('2026-01-01T00:00:00.000Z'),
      ),
    ).toBe('UPCOMING');
    expect(
      effectiveContestStatus(
        { ...base, status: 'UPCOMING' },
        new Date('2026-01-02T12:00:00.000Z'),
      ),
    ).toBe('RUNNING');
    expect(
      effectiveContestStatus(
        { ...base, status: 'UPCOMING' },
        new Date('2026-01-04T00:00:00.000Z'),
      ),
    ).toBe('ENDED');
  });
});
