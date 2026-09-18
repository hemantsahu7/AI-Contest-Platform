import {
  ConflictException,
  ForbiddenException,
  Injectable,
  Logger,
  NotFoundException,
} from '@nestjs/common';
import { Prisma } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { AuthUser } from '../common/types/auth-user';
import { isGlobalAdmin, requireOrgMember, requireOrgManager } from '../common/utils/access';
import { CreateOrganizationDto } from './dto/create-organization.dto';
import { AddMemberDto } from './dto/add-member.dto';

@Injectable()
export class OrganizationsService {
  private readonly logger = new Logger(OrganizationsService.name);

  constructor(private readonly prisma: PrismaService) {}

  async create(user: AuthUser, dto: CreateOrganizationDto) {
    if (!isGlobalAdmin(user)) {
      throw new ForbiddenException('Only a global admin can create organizations');
    }
    const org = await this.prisma.organization.create({
      data: {
        name: dto.name,
        memberships: {
          create: { userId: user.id, role: 'ADMIN' },
        },
      },
    });
    this.logger.log(`Organization created ${org.id} by ${user.username}`);
    return org;
  }

  async getById(user: AuthUser, id: string) {
    const org = await this.prisma.organization.findUnique({ where: { id } });
    if (!org) {
      throw new NotFoundException('Organization not found');
    }
    requireOrgMember(user, id);
    return org;
  }

  async listMembers(user: AuthUser, id: string) {
    await this.getById(user, id);
    return this.prisma.organizationMembership.findMany({
      where: { organizationId: id },
      include: {
        user: { select: { id: true, username: true, email: true, role: true } },
      },
    });
  }

  async addMember(user: AuthUser, id: string, dto: AddMemberDto) {
    requireOrgManager(user, id);
    const org = await this.prisma.organization.findUnique({ where: { id } });
    if (!org) {
      throw new NotFoundException('Organization not found');
    }
    const target = await this.prisma.user.findUnique({ where: { id: dto.userId } });
    if (!target) {
      throw new NotFoundException('User not found');
    }
    try {
      return await this.prisma.organizationMembership.create({
        data: {
          userId: dto.userId,
          organizationId: id,
          role: dto.role,
        },
      });
    } catch (error) {
      if (error instanceof Prisma.PrismaClientKnownRequestError && error.code === 'P2002') {
        throw new ConflictException('User is already a member of this organization');
      }
      throw error;
    }
  }
}
