import { Processor, WorkerHost, OnWorkerEvent } from '@nestjs/bullmq';
import { Logger } from '@nestjs/common';
import { Job } from 'bullmq';
import {
  IncidentType,
  JudgeExecutionStatus,
  SubmissionStatus,
  Verdict,
} from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { DockerRunnerService } from './docker-runner.service';
import { SUBMISSION_QUEUE } from '../queue/queue.module';
import { ConfigService } from '@nestjs/config';
import { shouldSkipJudging } from './idempotency';

@Processor(SUBMISSION_QUEUE)
export class JudgeProcessor extends WorkerHost {
  private readonly logger = new Logger(JudgeProcessor.name);
  private readonly judgeVersionName: string;

  constructor(
    private readonly prisma: PrismaService,
    private readonly dockerRunner: DockerRunnerService,
    config: ConfigService,
  ) {
    super();
    this.judgeVersionName = config.get<string>('JUDGE_VERSION', 'judge-v1');
  }

  async process(job: Job<{ submissionId: string }>): Promise<void> {
    const { submissionId } = job.data;
    this.logger.log(`Judge start submission=${submissionId} attempt=${job.attemptsMade + 1}`);

    const submission = await this.prisma.submission.findUnique({
      where: { id: submissionId },
      include: {
        problem: { include: { testCases: true } },
      },
    });

    if (!submission) {
      this.logger.warn(`Submission ${submissionId} missing; skipping`);
      return;
    }

    if (shouldSkipJudging(submission.status)) {
      this.logger.log(`Skip already-final submission ${submissionId} status=${submission.status}`);
      return;
    }

    const claimed = await this.prisma.submission.updateMany({
      where: {
        id: submissionId,
        status: { in: [SubmissionStatus.QUEUED, SubmissionStatus.RUNNING] },
      },
      data: { status: SubmissionStatus.RUNNING, verdict: Verdict.PENDING },
    });
    if (claimed.count === 0) {
      this.logger.log(`Could not claim submission ${submissionId}; skipping`);
      return;
    }

    const version = await this.prisma.judgeVersion.findUnique({
      where: { version: this.judgeVersionName },
    });
    if (!version) {
      throw new Error(`JudgeVersion ${this.judgeVersionName} is not seeded`);
    }

    const execution = await this.prisma.judgeExecution.create({
      data: {
        submissionId,
        judgeVersionId: version.id,
        startedAt: new Date(),
        status: JudgeExecutionStatus.RUNNING,
        verdict: Verdict.PENDING,
        testsTotal: submission.problem.testCases.length,
      },
    });

    try {
      const result = await this.dockerRunner.runCpp({
        sourceCode: submission.sourceCode,
        timeLimitMs: submission.problem.timeLimitMs,
        memoryLimitMb: submission.problem.memoryLimitMb,
        tests: submission.problem.testCases.map((t) => ({
          input: t.input,
          expectedOutput: t.expectedOutput,
        })),
      });

      if (result.kind === 'infrastructure') {
        await this.prisma.judgeExecution.update({
          where: { id: execution.id },
          data: {
            finishedAt: new Date(),
            status: JudgeExecutionStatus.FAILED,
            verdict: Verdict.JUDGE_ERROR,
            errorMessage: result.message.slice(0, 2000),
          },
        });
        await this.prisma.judgeIncident.create({
          data: {
            submissionId,
            judgeExecutionId: execution.id,
            type: result.type,
            message: result.message.slice(0, 2000),
          },
        });
        this.logger.error(
          `Judge infrastructure failure submission=${submissionId} type=${result.type}`,
        );
        throw new Error(result.message);
      }

      const score =
        result.verdict === 'ACCEPTED' ? submission.problem.points : 0;

      await this.prisma.$transaction(async (tx) => {
        await tx.judgeExecution.update({
          where: { id: execution.id },
          data: {
            finishedAt: new Date(),
            status: JudgeExecutionStatus.COMPLETED,
            verdict: result.verdict,
            executionTimeMs: result.executionTimeMs,
            memoryUsedMb: result.memoryUsedMb,
            testsPassed: result.testsPassed,
            testsTotal: result.testsTotal,
            stdout: result.stdout,
            stderr: result.stderr,
          },
        });
        await tx.submission.updateMany({
          where: {
            id: submissionId,
            status: SubmissionStatus.RUNNING,
          },
          data: {
            status: SubmissionStatus.COMPLETED,
            verdict: result.verdict,
            score,
            completedAt: new Date(),
          },
        });
      });

      this.logger.log(
        `Judge completion submission=${submissionId} verdict=${result.verdict} score=${score}`,
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      if (!message.includes(submissionId) && job.attemptsMade === 0) {
        this.logger.warn(`Judge retry path submission=${submissionId}: ${message}`);
      }
      throw error;
    }
  }

  @OnWorkerEvent('failed')
  async onFailed(job: Job<{ submissionId: string }> | undefined, error: Error) {
    if (!job) {
      return;
    }
    const attempts = job.opts.attempts ?? 1;
    this.logger.error(
      `Judge failure submission=${job.data.submissionId} attempt=${job.attemptsMade}/${attempts}: ${error.message}`,
    );
    if (job.attemptsMade >= attempts) {
      const submissionId = job.data.submissionId;
      await this.prisma.submission.updateMany({
        where: {
          id: submissionId,
          status: { in: [SubmissionStatus.QUEUED, SubmissionStatus.RUNNING] },
        },
        data: {
          status: SubmissionStatus.INFRASTRUCTURE_ERROR,
          verdict: Verdict.JUDGE_ERROR,
          completedAt: new Date(),
        },
      });
      await this.prisma.judgeIncident.create({
        data: {
          submissionId,
          type: IncidentType.WORKER_FAILURE,
          message: `Retries exhausted: ${error.message}`.slice(0, 2000),
        },
      });
    }
  }
}
