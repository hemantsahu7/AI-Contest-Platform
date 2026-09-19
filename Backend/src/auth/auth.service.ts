import {
  ConflictException,
  Injectable,
  Logger,
  UnauthorizedException,
} from '@nestjs/common';
import { JwtService } from '@nestjs/jwt';
import { GlobalRole } from '@prisma/client';
import * as bcrypt from 'bcrypt';
import { PrismaService } from '../prisma/prisma.service';
import { RegisterDto } from './dto/register.dto';
import { LoginDto } from './dto/login.dto';
import { AuthUser } from '../common/types/auth-user';

const SALT_ROUNDS = 10;

@Injectable()
export class AuthService {
  private readonly logger = new Logger(AuthService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly jwt: JwtService,
  ) {}

  async register(dto: RegisterDto) {
    const existing = await this.prisma.user.findFirst({
      where: { OR: [{ email: dto.email }, { username: dto.username }] },
    });
    if (existing) {
      throw new ConflictException('Username or email is already registered');
    }

    const passwordHash = await bcrypt.hash(dto.password, SALT_ROUNDS);
    const user = await this.prisma.user.create({
      data: {
        username: dto.username,
        email: dto.email,
        passwordHash,
        role: GlobalRole.LEARNER,
      },
      include: { memberships: true },
    });

    this.logger.log(`Registered user ${user.username}`);
    return this.signUser(user);
  }

  async login(dto: LoginDto) {
    const user = await this.prisma.user.findUnique({
      where: { email: dto.email },
      include: { memberships: true },
    });
    if (!user) {
      this.logger.warn(`Failed login for unknown email`);
      throw new UnauthorizedException('Invalid credentials');
    }
    const ok = await bcrypt.compare(dto.password, user.passwordHash);
    if (!ok) {
      this.logger.warn(`Failed login for ${user.username}`);
      throw new UnauthorizedException('Invalid credentials');
    }
    this.logger.log(`Login succeeded for ${user.username}`);
    return this.signUser(user);
  }

  me(user: AuthUser) {
    return {
      id: user.id,
      username: user.username,
      email: user.email,
      role: user.role,
      memberships: user.memberships,
    };
  }

  private signUser(user: {
    id: string;
    username: string;
    email: string;
    role: GlobalRole;
    memberships: { organizationId: string; role: GlobalRole }[];
  }) {
    const accessToken = this.jwt.sign({
      sub: user.id,
      username: user.username,
      role: user.role,
    });
    return {
      accessToken,
      user: {
        id: user.id,
        username: user.username,
        email: user.email,
        role: user.role,
        memberships: user.memberships.map((m) => ({
          organizationId: m.organizationId,
          role: m.role,
        })),
      },
    };
  }
}
