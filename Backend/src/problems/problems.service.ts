import { Injectable, Logger, NotFoundException } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { AuthUser } from '../common/types/auth-user';
import { canManageOrganization, requireOrgManager } from '../common/utils/access';
import { ContestsService } from '../contests/contests.service';
import { CreateProblemDto } from './dto/create-problem.dto';
import { UpdateProblemDto } from './dto/update-problem.dto';
import { CreateTestCaseDto } from './dto/create-test-case.dto';

@Injectable()
export class ProblemsService {
  private readonly logger = new Logger(ProblemsService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly contests: ContestsService,
  ) {}

  async create(user: AuthUser, contestId: string, dto: CreateProblemDto) {
    const contest = await this.contests.getById(user, contestId);
    requireOrgManager(user, contest.organizationId);
    const problem = await this.prisma.problem.create({
      data: { ...dto, contestId },
    });
    this.logger.log(`Problem created ${problem.id} in contest ${contestId}`);
    return problem;
  }

  async list(user: AuthUser, contestId: string) {
    await this.contests.getById(user, contestId);
    const problems = await this.prisma.problem.findMany({
      where: { contestId },
      include: { testCases: true, contest: { select: { organizationId: true } } },
      orderBy: { createdAt: 'asc' },
    });
    return problems.map((p) => this.sanitizeProblem(user, p));
  }

  async getById(user: AuthUser, contestId: string, problemId: string) {
    await this.contests.getById(user, contestId);
    const problem = await this.prisma.problem.findFirst({
      where: { id: problemId, contestId },
      include: { testCases: true, contest: { select: { organizationId: true } } },
    });
    if (!problem) {
      throw new NotFoundException('Problem not found');
    }
    return this.sanitizeProblem(user, problem);
  }

  async update(user: AuthUser, contestId: string, problemId: string, dto: UpdateProblemDto) {
    const existing = await this.getManagedProblem(user, contestId, problemId);
    return this.prisma.problem.update({
      where: { id: existing.id },
      data: dto,
    });
  }

  async addTestCase(user: AuthUser, problemId: string, dto: CreateTestCaseDto) {
    const problem = await this.prisma.problem.findUnique({
      where: { id: problemId },
      include: { contest: true },
    });
    if (!problem) {
      throw new NotFoundException('Problem not found');
    }
    requireOrgManager(user, problem.contest.organizationId);
    return this.prisma.testCase.create({
      data: {
        problemId,
        input: dto.input,
        expectedOutput: dto.expectedOutput,
        isHidden: dto.isHidden ?? true,
      },
    });
  }

  private async getManagedProblem(user: AuthUser, contestId: string, problemId: string) {
    const contest = await this.contests.getById(user, contestId);
    requireOrgManager(user, contest.organizationId);
    const problem = await this.prisma.problem.findFirst({
      where: { id: problemId, contestId },
    });
    if (!problem) {
      throw new NotFoundException('Problem not found');
    }
    return problem;
  }

  private sanitizeProblem(
    user: AuthUser,
    problem: {
      contest?: { organizationId: string };
      contestId: string;
      testCases: {
        id: string;
        input: string;
        expectedOutput: string;
        isHidden: boolean;
        createdAt: Date;
      }[];
      [key: string]: unknown;
    },
  ) {
    const orgId =
      problem.contest?.organizationId ??
      (typeof problem['organizationId'] === 'string' ? problem['organizationId'] : undefined);
    const hideSecrets = orgId ? !canManageOrganization(user, orgId) : true;
    const { testCases, contest, ...rest } = problem;
    const visibleTests = hideSecrets
      ? testCases
          .filter((t) => !t.isHidden)
          .map((t) => ({
            id: t.id,
            input: t.input,
            expectedOutput: t.expectedOutput,
            isHidden: false,
            createdAt: t.createdAt,
          }))
      : testCases;
    return { ...rest, testCases: visibleTests };
  }
}
