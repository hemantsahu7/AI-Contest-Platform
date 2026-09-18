import { Injectable } from '@nestjs/common';
import { Verdict } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { AuthUser } from '../common/types/auth-user';
import { ContestsService } from '../contests/contests.service';
import { buildLeaderboard } from './leaderboard.util';

@Injectable()
export class LeaderboardService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly contests: ContestsService,
  ) {}

  async get(user: AuthUser, contestId: string) {
    const contest = await this.contests.getById(user, contestId);
    const participants = await this.prisma.contestParticipant.findMany({
      where: { contestId },
      include: { user: { select: { username: true } } },
    });
    const accepted = await this.prisma.submission.findMany({
      where: {
        contestId,
        status: 'COMPLETED',
        verdict: Verdict.ACCEPTED,
      },
      include: { problem: { select: { points: true } }, user: { select: { username: true } } },
    });

    return buildLeaderboard(
      accepted.map((s) => ({
        userId: s.userId,
        username: s.user.username,
        problemId: s.problemId,
        points: s.problem.points,
        acceptedAt: s.completedAt ?? s.submittedAt,
      })),
      contest.startTime,
      participants.map((p) => ({ userId: p.userId, username: p.user.username })),
    ).map(({ userId: _userId, ...rest }) => rest);
  }
}
