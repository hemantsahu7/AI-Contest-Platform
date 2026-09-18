import { SubmissionStatus } from '@prisma/client';

export function shouldSkipJudging(status: SubmissionStatus): boolean {
  return (
    status === SubmissionStatus.COMPLETED ||
    status === SubmissionStatus.INFRASTRUCTURE_ERROR
  );
}
