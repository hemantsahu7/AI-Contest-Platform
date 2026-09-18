import { Contest, ContestStatus } from '@prisma/client';

export function effectiveContestStatus(
  contest: Pick<Contest, 'status' | 'startTime' | 'endTime'>,
  now = new Date(),
): ContestStatus {
  if (contest.status === ContestStatus.DRAFT) {
    return ContestStatus.DRAFT;
  }
  if (now < contest.startTime) {
    return ContestStatus.UPCOMING;
  }
  if (now > contest.endTime) {
    return ContestStatus.ENDED;
  }
  return ContestStatus.RUNNING;
}

export function withEffectiveStatus<T extends Pick<Contest, 'status' | 'startTime' | 'endTime'>>(
  contest: T,
  now = new Date(),
): T & { status: ContestStatus } {
  return { ...contest, status: effectiveContestStatus(contest, now) };
}
