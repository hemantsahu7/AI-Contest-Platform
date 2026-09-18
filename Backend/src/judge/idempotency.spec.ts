import { shouldSkipJudging } from './idempotency';
import { SubmissionStatus } from '@prisma/client';

describe('judge idempotency', () => {
  it('does not re-judge a completed or infrastructure-final submission', () => {
    expect(shouldSkipJudging(SubmissionStatus.COMPLETED)).toBe(true);
    expect(shouldSkipJudging(SubmissionStatus.INFRASTRUCTURE_ERROR)).toBe(true);
    expect(shouldSkipJudging(SubmissionStatus.QUEUED)).toBe(false);
    expect(shouldSkipJudging(SubmissionStatus.RUNNING)).toBe(false);
  });
});
