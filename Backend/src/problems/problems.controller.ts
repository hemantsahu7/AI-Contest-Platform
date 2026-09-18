import { Body, Controller, Get, Param, Patch, Post } from '@nestjs/common';
import { ApiBearerAuth, ApiTags } from '@nestjs/swagger';
import { GlobalRole } from '@prisma/client';
import { ProblemsService } from './problems.service';
import { CreateProblemDto } from './dto/create-problem.dto';
import { UpdateProblemDto } from './dto/update-problem.dto';
import { CreateTestCaseDto } from './dto/create-test-case.dto';
import { CurrentUser } from '../common/decorators/current-user.decorator';
import { AuthUser } from '../common/types/auth-user';
import { Roles } from '../common/decorators/roles.decorator';

@ApiTags('problems')
@ApiBearerAuth()
@Controller()
export class ProblemsController {
  constructor(private readonly problems: ProblemsService) {}

  @Roles(GlobalRole.ADMIN, GlobalRole.INSTRUCTOR)
  @Post('contests/:contestId/problems')
  create(
    @CurrentUser() user: AuthUser,
    @Param('contestId') contestId: string,
    @Body() dto: CreateProblemDto,
  ) {
    return this.problems.create(user, contestId, dto);
  }

  @Get('contests/:contestId/problems')
  list(@CurrentUser() user: AuthUser, @Param('contestId') contestId: string) {
    return this.problems.list(user, contestId);
  }

  @Get('contests/:contestId/problems/:problemId')
  getById(
    @CurrentUser() user: AuthUser,
    @Param('contestId') contestId: string,
    @Param('problemId') problemId: string,
  ) {
    return this.problems.getById(user, contestId, problemId);
  }

  @Roles(GlobalRole.ADMIN, GlobalRole.INSTRUCTOR)
  @Patch('contests/:contestId/problems/:problemId')
  update(
    @CurrentUser() user: AuthUser,
    @Param('contestId') contestId: string,
    @Param('problemId') problemId: string,
    @Body() dto: UpdateProblemDto,
  ) {
    return this.problems.update(user, contestId, problemId, dto);
  }

  @Roles(GlobalRole.ADMIN, GlobalRole.INSTRUCTOR)
  @Post('problems/:problemId/test-cases')
  addTestCase(
    @CurrentUser() user: AuthUser,
    @Param('problemId') problemId: string,
    @Body() dto: CreateTestCaseDto,
  ) {
    return this.problems.addTestCase(user, problemId, dto);
  }
}
