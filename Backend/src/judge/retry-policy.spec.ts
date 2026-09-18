import { shouldRetryVerdict } from './retry-policy';
import { Verdict } from '@prisma/client';

describe('retry policy', () => {
  it('does not retry deterministic student errors', () => {
    expect(shouldRetryVerdict(Verdict.WRONG_ANSWER)).toBe(false);
    expect(shouldRetryVerdict(Verdict.COMPILATION_ERROR)).toBe(false);
    expect(shouldRetryVerdict(Verdict.RUNTIME_ERROR)).toBe(false);
    expect(shouldRetryVerdict(Verdict.TIME_LIMIT_EXCEEDED)).toBe(false);
    expect(shouldRetryVerdict(Verdict.ACCEPTED)).toBe(false);
  });

  it('allows retry only for judge/infrastructure errors', () => {
    expect(shouldRetryVerdict(Verdict.JUDGE_ERROR)).toBe(true);
  });
});
