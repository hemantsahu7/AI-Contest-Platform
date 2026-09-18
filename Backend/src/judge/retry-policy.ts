import { Verdict } from '@prisma/client';

export type StudentJudgeOutcome =
  | { verdict: typeof Verdict.COMPILATION_ERROR }
  | { verdict: typeof Verdict.RUNTIME_ERROR }
  | { verdict: typeof Verdict.TIME_LIMIT_EXCEEDED }
  | { verdict: typeof Verdict.WRONG_ANSWER }
  | { verdict: typeof Verdict.ACCEPTED };

export function isRetryableInfrastructure(kind: string): boolean {
  return [
    'CONTAINER_START_FAILURE',
    'DOCKER_ERROR',
    'WORKER_FAILURE',
    'TIMEOUT',
    'UNKNOWN',
  ].includes(kind);
}

export function shouldRetryVerdict(verdict: Verdict): boolean {
  return verdict === Verdict.JUDGE_ERROR;
}
