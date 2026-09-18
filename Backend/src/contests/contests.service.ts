import {
  BadRequestException,
  ConflictException,
  ForbiddenException,
  Injectable,
  Logger,
  NotFoundException,
} from '@nestjs/common';
import { ContestStatus, Prisma } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { AuthUser } from '../common/types/auth-user';
import {
  canManageOrganization,
  requireOrgManager,
  requireOrgMember,
} from '../common/utils/access';
import { effectiveContestStatus, withEffectiveStatus } from '../common/utils/contest-status';
import { CreateContestDto } from './dto/create-contest.dto';
import { UpdateContestDto } from './dto/update-contest.dto';

@Injectable()
export class ContestsService {
  private readonly logger = new Logger(ContestsService.name);

  constructor(private readonly prisma: PrismaService) {}

  async create(user: AuthUser, dto: CreateContestDto) {
    requireOrgManager(user, dto.organizationId);
    const org = await this.prisma.organization.findUnique({
      where: { id: dto.organizationId },
    });
    if (!org) {
      throw new NotFoundException('Organization not found');
    }
    const start = new Date(dto.startTime);
    const end = new Date(dto.endTime);
    if (!(start < end)) {
      throw new BadRequestException('startTime must be before endTime');
    }
    const contest = await this.prisma.contest.create({
      data: {
        title: dto.title,
        description: dto.description,
        organizationId: dto.organizationId,
        startTime: start,
        endTime: end,
        status: dto.status ?? ContestStatus.DRAFT,
      },
    });
    this.logger.log(`Contest created ${contest.id} by ${user.username}`);
    return withEffectiveStatus(contest);
  }

  async list(user: AuthUser) {
    const orgIds = user.memberships.map((m) => m.organizationId);
    const contests = await this.prisma.contest.findMany({
      where:
        user.role === 'ADMIN'
          ? undefined
          : { organizationId: { in: orgIds } },
      orderBy: { startTime: 'asc' },
    });
    return contests
      .map((c) => withEffectiveStatus(c))
      .filter((c) => {
        if (canManageOrganization(user, c.organizationId)) {
          return true;
        }
        return c.status !== ContestStatus.DRAFT;
      });
  }

  async getById(user: AuthUser, id: string) {
    const contest = await this.prisma.contest.findUnique({ where: { id } });
    if (!contest) {
      throw new NotFoundException('Contest not found');
    }
    requireOrgMember(user, contest.organizationId);
    const view = withEffectiveStatus(contest);
    if (view.status === ContestStatus.DRAFT && !canManageOrganization(user, contest.organizationId)) {
      throw new NotFoundException('Contest not found');
    }
    return view;
  }

  async update(user: AuthUser, id: string, dto: UpdateContestDto) {
    const contest = await this.prisma.contest.findUnique({ where: { id } });
    if (!contest) {
      throw new NotFoundException('Contest not found');
    }
    requireOrgManager(user, contest.organizationId);
    const start = dto.startTime ? new Date(dto.startTime) : contest.startTime;
    const end = dto.endTime ? new Date(dto.endTime) : contest.endTime;
    if (!(start < end)) {
      throw new BadRequestException('startTime must be before endTime');
    }
    const updated = await this.prisma.contest.update({
      where: { id },
      data: {
        title: dto.title,
        description: dto.description,
        startTime: start,
        endTime: end,
        status: dto.status,
      },
    });
    return withEffectiveStatus(updated);
  }

  async join(user: AuthUser, id: string) {
    const contest = await this.getById(user, id);
    const status = contest.status;
    if (status !== ContestStatus.UPCOMING && status !== ContestStatus.RUNNING) {
      throw new ForbiddenException('Contest is not open for joining');
    }
    try {
      const participant = await this.prisma.contestParticipant.create({
        data: { contestId: id, userId: user.id },
      });
      this.logger.log(`User ${user.username} joined contest ${id}`);
      return participant;
    } catch (error) {
      if (error instanceof Prisma.PrismaClientKnownRequestError && error.code === 'P2002') {
        throw new ConflictException('Already joined this contest');
      }
      throw error;
    }
  }

  async participants(user: AuthUser, id: string) {
    const contest = await this.getById(user, id);
    return this.prisma.contestParticipant.findMany({
      where: { contestId: contest.id },
      include: { user: { select: { id: true, username: true } } },
      orderBy: { joinedAt: 'asc' },
    });
  }
}
