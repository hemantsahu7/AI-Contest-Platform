import { Body, Controller, Get, Param, Patch, Post } from '@nestjs/common';
import { ApiBearerAuth, ApiTags } from '@nestjs/swagger';
import { GlobalRole } from '@prisma/client';
import { ContestsService } from './contests.service';
import { CreateContestDto } from './dto/create-contest.dto';
import { UpdateContestDto } from './dto/update-contest.dto';
import { CurrentUser } from '../common/decorators/current-user.decorator';
import { AuthUser } from '../common/types/auth-user';
import { Roles } from '../common/decorators/roles.decorator';

@ApiTags('contests')
@ApiBearerAuth()
@Controller('contests')
export class ContestsController {
  constructor(private readonly contests: ContestsService) {}

  @Roles(GlobalRole.ADMIN, GlobalRole.INSTRUCTOR)
  @Post()
  create(@CurrentUser() user: AuthUser, @Body() dto: CreateContestDto) {
    return this.contests.create(user, dto);
  }

  @Get()
  list(@CurrentUser() user: AuthUser) {
    return this.contests.list(user);
  }

  @Get(':id')
  getById(@CurrentUser() user: AuthUser, @Param('id') id: string) {
    return this.contests.getById(user, id);
  }

  @Roles(GlobalRole.ADMIN, GlobalRole.INSTRUCTOR)
  @Patch(':id')
  update(
    @CurrentUser() user: AuthUser,
    @Param('id') id: string,
    @Body() dto: UpdateContestDto,
  ) {
    return this.contests.update(user, id, dto);
  }

  @Post(':id/join')
  join(@CurrentUser() user: AuthUser, @Param('id') id: string) {
    return this.contests.join(user, id);
  }

  @Get(':id/participants')
  participants(@CurrentUser() user: AuthUser, @Param('id') id: string) {
    return this.contests.participants(user, id);
  }
}
