import { PrismaClient, ContestStatus, Difficulty, GlobalRole, IncidentType, JudgeExecutionStatus, SubmissionStatus, Verdict } from '@prisma/client';
import * as bcrypt from 'bcrypt';

const prisma = new PrismaClient();

export const SEED_IDS = {
  orgA: '11111111-1111-4111-8111-111111111111',
  orgB: '11111111-1111-4111-8111-111111111112',
  admin: '22222222-2222-4222-8222-222222222221',
  instructor: '22222222-2222-4222-8222-222222222222',
  learner1: '22222222-2222-4222-8222-222222222223',
  learner2: '22222222-2222-4222-8222-222222222224',
  instructorB: '22222222-2222-4222-8222-222222222225',
  learnerB: '22222222-2222-4222-8222-222222222226',
  contestOpen: '33333333-3333-4333-8333-333333333331',
  contestEnded: '33333333-3333-4333-8333-333333333332',
  contestB: '33333333-3333-4333-8333-333333333333',
  problemSum: '44444444-4444-4444-8444-444444444441',
  problemMax: '44444444-4444-4444-8444-444444444442',
  problemDiff: '44444444-4444-4444-8444-444444444443',
  problemMul: '44444444-4444-4444-8444-444444444444',
  endedProblem: '44444444-4444-4444-8444-444444444449',
  judgeVersion: '55555555-5555-4555-8555-555555555551',
  submissionLearner1Sum: '66666666-6666-4666-8666-666666666661',
  submissionLearner1Max: '66666666-6666-4666-8666-666666666662',
  submissionLearner2Sum: '66666666-6666-4666-8666-666666666663',
  submissionInfra: '66666666-6666-4666-8666-666666666669',
};

const PASSWORD = 'Password123!';

const SUM_AC = `#include <iostream>
int main() {
  long long a, b;
  std::cin >> a >> b;
  std::cout << (a + b) << std::endl;
  return 0;
}
`;

async function upsertUser(id: string, username: string, email: string, role: GlobalRole, passwordHash: string) {
  return prisma.user.upsert({
    where: { email },
    update: { username, role, passwordHash },
    create: { id, username, email, role, passwordHash },
  });
}

async function main() {
  const passwordHash = await bcrypt.hash(PASSWORD, 10);

  await prisma.organization.upsert({
    where: { id: SEED_IDS.orgA },
    update: { name: 'Shodh Academy' },
    create: { id: SEED_IDS.orgA, name: 'Shodh Academy' },
  });
  await prisma.organization.upsert({
    where: { id: SEED_IDS.orgB },
    update: { name: 'Other Institute' },
    create: { id: SEED_IDS.orgB, name: 'Other Institute' },
  });

  await upsertUser(SEED_IDS.admin, 'admin', 'admin@example.com', GlobalRole.ADMIN, passwordHash);
  await upsertUser(SEED_IDS.instructor, 'instructor', 'instructor@example.com', GlobalRole.INSTRUCTOR, passwordHash);
  await upsertUser(SEED_IDS.learner1, 'learner1', 'learner1@example.com', GlobalRole.LEARNER, passwordHash);
  await upsertUser(SEED_IDS.learner2, 'learner2', 'learner2@example.com', GlobalRole.LEARNER, passwordHash);
  await upsertUser(SEED_IDS.instructorB, 'instructorB', 'instructor-b@example.com', GlobalRole.INSTRUCTOR, passwordHash);
  await upsertUser(SEED_IDS.learnerB, 'learnerB', 'learner-b@example.com', GlobalRole.LEARNER, passwordHash);

  const memberships: { userId: string; organizationId: string; role: GlobalRole }[] = [
    { userId: SEED_IDS.admin, organizationId: SEED_IDS.orgA, role: GlobalRole.ADMIN },
    { userId: SEED_IDS.instructor, organizationId: SEED_IDS.orgA, role: GlobalRole.INSTRUCTOR },
    { userId: SEED_IDS.learner1, organizationId: SEED_IDS.orgA, role: GlobalRole.LEARNER },
    { userId: SEED_IDS.learner2, organizationId: SEED_IDS.orgA, role: GlobalRole.LEARNER },
    { userId: SEED_IDS.instructorB, organizationId: SEED_IDS.orgB, role: GlobalRole.INSTRUCTOR },
    { userId: SEED_IDS.learnerB, organizationId: SEED_IDS.orgB, role: GlobalRole.LEARNER },
  ];
  for (const m of memberships) {
    await prisma.organizationMembership.upsert({
      where: { userId_organizationId: { userId: m.userId, organizationId: m.organizationId } },
      update: { role: m.role },
      create: m,
    });
  }

  await prisma.contest.upsert({
    where: { id: SEED_IDS.contestOpen },
    update: {},
    create: {
      id: SEED_IDS.contestOpen,
      title: 'Shodh Open Contest',
      description: 'Demo contest that stays running (2020-2099) so reviewers can submit code.',
      organizationId: SEED_IDS.orgA,
      startTime: new Date('2020-01-01T00:00:00.000Z'),
      endTime: new Date('2099-01-01T00:00:00.000Z'),
      status: ContestStatus.UPCOMING,
    },
  });
  await prisma.contest.upsert({
    where: { id: SEED_IDS.contestEnded },
    update: {},
    create: {
      id: SEED_IDS.contestEnded,
      title: 'Archive Contest',
      description: 'Already ended. Used to demonstrate rejected late submissions.',
      organizationId: SEED_IDS.orgA,
      startTime: new Date('2020-01-01T00:00:00.000Z'),
      endTime: new Date('2020-01-02T00:00:00.000Z'),
      status: ContestStatus.UPCOMING,
    },
  });
  await prisma.contest.upsert({
    where: { id: SEED_IDS.contestB },
    update: {},
    create: {
      id: SEED_IDS.contestB,
      title: 'Org B Contest',
      description: 'Restricted to Other Institute.',
      organizationId: SEED_IDS.orgB,
      startTime: new Date('2020-01-01T00:00:00.000Z'),
      endTime: new Date('2099-01-01T00:00:00.000Z'),
      status: ContestStatus.UPCOMING,
    },
  });

  const problems = [
    {
      id: SEED_IDS.problemSum,
      title: 'Sum of Two Numbers',
      description: 'Read two integers A and B and print A+B. Constraints: -5000000000 <= A, B <= 5000000000.',
      difficulty: Difficulty.EASY,
      points: 100,
      timeLimitMs: 1000,
      memoryLimitMb: 64,
      inputFormat: 'Two integers A and B.',
      outputFormat: 'A single integer, A+B.',
      tests: [
        { input: '1 2\n', expectedOutput: '3\n', isHidden: false },
        { input: '10 20\n', expectedOutput: '30\n', isHidden: false },
        { input: '2000000000 2000000000\n', expectedOutput: '4000000000\n', isHidden: true },
        { input: '-5 8\n', expectedOutput: '3\n', isHidden: true },
      ],
    },
    {
      id: SEED_IDS.problemMax,
      title: 'Maximum of Two',
      description: 'Read two integers and print the larger one. Constraints: -1000000000 <= A, B <= 1000000000.',
      difficulty: Difficulty.EASY,
      points: 100,
      timeLimitMs: 1000,
      memoryLimitMb: 64,
      inputFormat: 'Two integers A and B.',
      outputFormat: 'max(A, B)',
      tests: [
        { input: '1 2\n', expectedOutput: '2\n', isHidden: false },
        { input: '9 3\n', expectedOutput: '9\n', isHidden: true },
      ],
    },
    {
      id: SEED_IDS.problemDiff,
      title: 'Absolute Difference',
      description: 'Read two integers and print |A-B|. Constraints: -1000000000 <= A, B <= 1000000000.',
      difficulty: Difficulty.EASY,
      points: 100,
      timeLimitMs: 1000,
      memoryLimitMb: 64,
      inputFormat: 'Two integers A and B.',
      outputFormat: '|A-B|',
      tests: [
        { input: '5 2\n', expectedOutput: '3\n', isHidden: false },
        { input: '2 5\n', expectedOutput: '3\n', isHidden: true },
      ],
    },
    {
      id: SEED_IDS.problemMul,
      title: 'Product',
      description: 'Read two integers and print A*B. Constraints: -1000000000 <= A, B <= 1000000000. Time limit is tight enough for timeout demos with an infinite loop.',
      difficulty: Difficulty.MEDIUM,
      points: 150,
      timeLimitMs: 500,
      memoryLimitMb: 64,
      inputFormat: 'Two integers A and B.',
      outputFormat: 'A*B',
      tests: [
        { input: '3 4\n', expectedOutput: '12\n', isHidden: false },
        { input: '7 8\n', expectedOutput: '56\n', isHidden: true },
      ],
    },
  ];

  for (const p of problems) {
    await prisma.problem.upsert({
      where: { id: p.id },
      update: {
        title: p.title,
        description: p.description,
        points: p.points,
        timeLimitMs: p.timeLimitMs,
      },
      create: {
        id: p.id,
        contestId: SEED_IDS.contestOpen,
        title: p.title,
        description: p.description,
        difficulty: p.difficulty,
        points: p.points,
        timeLimitMs: p.timeLimitMs,
        memoryLimitMb: p.memoryLimitMb,
        inputFormat: p.inputFormat,
        outputFormat: p.outputFormat,
      },
    });
    await prisma.testCase.deleteMany({ where: { problemId: p.id } });
    await prisma.testCase.createMany({
      data: p.tests.map((t) => ({ ...t, problemId: p.id })),
    });
  }

  await prisma.problem.upsert({
    where: { id: SEED_IDS.endedProblem },
    update: {},
    create: {
      id: SEED_IDS.endedProblem,
      contestId: SEED_IDS.contestEnded,
      title: 'Archive Sum',
      description: 'Ended-contest problem.',
      difficulty: Difficulty.EASY,
      points: 50,
      timeLimitMs: 1000,
      memoryLimitMb: 64,
      inputFormat: 'Two integers',
      outputFormat: 'Sum',
    },
  });
  await prisma.contestParticipant.upsert({
    where: {
      contestId_userId: { contestId: SEED_IDS.contestEnded, userId: SEED_IDS.learner1 },
    },
    update: {},
    create: { contestId: SEED_IDS.contestEnded, userId: SEED_IDS.learner1 },
  });

  await prisma.judgeVersion.upsert({
    where: { version: 'judge-v1' },
    update: { description: 'Deterministic C++ g++ judge' },
    create: {
      id: SEED_IDS.judgeVersion,
      version: 'judge-v1',
      description: 'Deterministic C++ g++ judge. AI never influences verdicts or scores.',
    },
  });
  const judgeVersion = await prisma.judgeVersion.findUniqueOrThrow({ where: { version: 'judge-v1' } });

  for (const userId of [SEED_IDS.learner1, SEED_IDS.learner2]) {
    await prisma.contestParticipant.upsert({
      where: { contestId_userId: { contestId: SEED_IDS.contestOpen, userId } },
      update: {},
      create: { contestId: SEED_IDS.contestOpen, userId },
    });
  }

  const historicalIds = [
    SEED_IDS.submissionLearner1Sum,
    SEED_IDS.submissionLearner1Max,
    SEED_IDS.submissionLearner2Sum,
    SEED_IDS.submissionInfra,
  ];
  await prisma.judgeIncident.deleteMany({ where: { submissionId: { in: historicalIds } } });
  await prisma.judgeExecution.deleteMany({ where: { submissionId: { in: historicalIds } } });
  await prisma.submission.deleteMany({ where: { id: { in: historicalIds } } });

  const historical = [
    {
      id: SEED_IDS.submissionLearner1Sum,
      userId: SEED_IDS.learner1,
      problemId: SEED_IDS.problemSum,
      score: 100,
      submittedAt: new Date('2020-01-01T01:00:00.000Z'),
      completedAt: new Date('2020-01-01T01:00:05.000Z'),
    },
    {
      id: SEED_IDS.submissionLearner1Max,
      userId: SEED_IDS.learner1,
      problemId: SEED_IDS.problemMax,
      score: 100,
      submittedAt: new Date('2020-01-01T01:10:00.000Z'),
      completedAt: new Date('2020-01-01T01:10:04.000Z'),
    },
    {
      id: SEED_IDS.submissionLearner2Sum,
      userId: SEED_IDS.learner2,
      problemId: SEED_IDS.problemSum,
      score: 100,
      submittedAt: new Date('2020-01-01T02:00:00.000Z'),
      completedAt: new Date('2020-01-01T02:00:06.000Z'),
    },
  ];

  for (const h of historical) {
    await prisma.submission.create({
      data: {
        id: h.id,
        contestId: SEED_IDS.contestOpen,
        problemId: h.problemId,
        userId: h.userId,
        sourceCode: SUM_AC,
        language: 'cpp',
        status: SubmissionStatus.COMPLETED,
        verdict: Verdict.ACCEPTED,
        score: h.score,
        submittedAt: h.submittedAt,
        completedAt: h.completedAt,
      },
    });
    await prisma.judgeExecution.create({
      data: {
        submissionId: h.id,
        judgeVersionId: judgeVersion.id,
        startedAt: h.submittedAt,
        finishedAt: h.completedAt,
        status: JudgeExecutionStatus.COMPLETED,
        verdict: Verdict.ACCEPTED,
        executionTimeMs: 20,
        memoryUsedMb: 64,
        testsPassed: 2,
        testsTotal: 2,
        stdout: 'ok',
      },
    });
  }

  await prisma.submission.create({
    data: {
      id: SEED_IDS.submissionInfra,
      contestId: SEED_IDS.contestOpen,
      problemId: SEED_IDS.problemMul,
      userId: SEED_IDS.learner2,
      sourceCode: '// historical failed infrastructure example',
      language: 'cpp',
      status: SubmissionStatus.INFRASTRUCTURE_ERROR,
      verdict: Verdict.JUDGE_ERROR,
      score: 0,
      submittedAt: new Date('2020-01-01T03:00:00.000Z'),
      completedAt: new Date('2020-01-01T03:00:10.000Z'),
    },
  });
  await prisma.judgeIncident.create({
    data: {
      submissionId: SEED_IDS.submissionInfra,
      type: IncidentType.CONTAINER_START_FAILURE,
      message: 'Example incident: judge container could not start. This is not a Wrong Answer.',
      resolvedAt: new Date('2020-01-01T03:05:00.000Z'),
    },
  });

  console.log('Seed complete. Demo password for all users: Password123!');
}

main()
  .catch((e) => {
    console.error(e);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
