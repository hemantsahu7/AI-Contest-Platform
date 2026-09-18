import {
  BadRequestException,
  ForbiddenException,
  Injectable,
  Logger,
  NotFoundException,
  PayloadTooLargeException,
} from '@nestjs/common';
import { ContestStatus } from '@prisma/client';
import { ConfigService } from '@nestjs/config';
import { PrismaService } from '../prisma/prisma.service';
import { AuthUser } from '../common/types/auth-user';
import { canInspectSubmissions } from '../common/utils/access';
import { ContestsService } from '../contests/contests.service';
import { JudgeService } from '../judge/judge.service';
import { CreateSubmissionDto } from './dto/create-submission.dto';

@Injectable()
export class SubmissionsService {
  private readonly logger = new Logger(SubmissionsService.name);
  private readonly maxSourceBytes: number;

  constructor(
    private readonly prisma: PrismaService,
    private readonly contests: ContestsService,
    private readonly judge: JudgeService,
    config: ConfigService,
  ) {
    this.maxSourceBytes = Number(config.get('MAX_SOURCE_CODE_BYTES') ?? 102400);
  }

  async create(
    user: AuthUser,
    contestId: string,
    problemId: string,
    dto: CreateSubmissionDto,
  ) {
    const contest = await this.contests.getById(user, contestId);
    if (contest.status !== ContestStatus.RUNNING) {
      throw new ForbiddenException('Submissions are only accepted while the contest is running');
    }
    const participant = await this.prisma.contestParticipant.findUnique({
      where: { contestId_userId: { contestId, userId: user.id } },
    });
    if (!participant) {
      throw new ForbiddenException('Join the contest before submitting');
    }
    const problem = await this.prisma.problem.findFirst({
      where: { id: problemId, contestId },
    });
    if (!problem) {
      throw new BadRequestException('Problem does not belong to this contest');
    }
    const bytes = Buffer.byteLength(dto.sourceCode, 'utf8');
    if (bytes > this.maxSourceBytes) {
      throw new PayloadTooLargeException(`Source code exceeds ${this.maxSourceBytes} bytes`);
    }

    const submission = await this.prisma.submission.create({
      data: {
        contestId,
        problemId,
        userId: user.id,
        sourceCode: dto.sourceCode,
        language: dto.language,
      },
    });
    this.logger.log(`Submission created ${submission.id} by ${user.username}`);
    try {
      await this.judge.enqueue(submission.id);
    } catch (error) {
      this.logger.error(
        `Failed to enqueue submission ${submission.id}: ${
          error instanceof Error ? error.message : String(error)
        }`,
      );
      await this.prisma.submission.update({
        where: { id: submission.id },
        data: { status: 'INFRASTRUCTURE_ERROR', verdict: 'JUDGE_ERROR', completedAt: new Date() },
      });
      await this.prisma.judgeIncident.create({
        data: {
          submissionId: submission.id,
          type: 'WORKER_FAILURE',
          message: 'Failed to enqueue judge job',
        },
      });
    }

    return {
      submissionId: submission.id,
      status: 'QUEUED',
    };
  }

  async getById(user: AuthUser, submissionId: string) {
    const submission = await this.prisma.submission.findUnique({
      where: { id: submissionId },
      include: {
        contest: true,
        problem: { select: { id: true, title: true, points: true } },
        executions: { orderBy: { createdAt: 'desc' }, take: 5 },
      },
    });
    if (!submission) {
      throw new NotFoundException('Submission not found');
    }
    const inspector = canInspectSubmissions(user, submission.contest.organizationId);
    if (!inspector && submission.userId !== user.id) {
      throw new NotFoundException('Submission not found');
    }
    return this.toResponse(submission, inspector || submission.userId === user.id, inspector);
  }

  async listForContest(user: AuthUser, contestId: string) {
    const contest = await this.contests.getById(user, contestId);
    const inspector = canInspectSubmissions(user, contest.organizationId);
    const submissions = await this.prisma.submission.findMany({
      where: inspector ? { contestId } : { contestId, userId: user.id },
      include: {
        contest: true,
        problem: { select: { id: true, title: true, points: true } },
        user: { select: { username: true } },
      },
      orderBy: { submittedAt: 'desc' },
    });
    return submissions.map((s) =>
      this.toResponse(s, inspector || s.userId === user.id, inspector),
    );
  }

  private toResponse(
    submission: {
      id: string;
      contestId: string;
      problemId: string;
      userId: string;
      sourceCode: string;
      language: string;
      status: string;
      verdict: string;
      score: number;
      submittedAt: Date;
      completedAt: Date | null;
      problem?: { id: string; title: string; points: number };
      user?: { username: string };
      executions?: {
        id: string;
        status: string;
        verdict: string;
        executionTimeMs: number | null;
        testsPassed: number | null;
        testsTotal: number | null;
        stdout: string | null;
        stderr: string | null;
        errorMessage: string | null;
        startedAt: Date;
        finishedAt: Date | null;
      }[];
    },
    includeSource: boolean,
    includeJudgeIo: boolean,
  ) {
    return {
      id: submission.id,
      contestId: submission.contestId,
      problemId: submission.problemId,
      userId: submission.userId,
      username: submission.user?.username,
      language: submission.language,
      status: submission.status,
      verdict: submission.verdict,
      score: submission.score,
      submittedAt: submission.submittedAt,
      completedAt: submission.completedAt,
      problem: submission.problem,
      sourceCode: includeSource ? submission.sourceCode : undefined,
      executions: submission.executions?.map((e) => ({
        id: e.id,
        status: e.status,
        verdict: e.verdict,
        executionTimeMs: e.executionTimeMs,
        testsPassed: e.testsPassed,
        testsTotal: e.testsTotal,
        startedAt: e.startedAt,
        finishedAt: e.finishedAt,
        stdout: includeJudgeIo ? e.stdout : undefined,
        stderr: includeJudgeIo ? e.stderr : undefined,
        errorMessage: includeJudgeIo ? e.errorMessage : undefined,
      })),
    };
  }
}
