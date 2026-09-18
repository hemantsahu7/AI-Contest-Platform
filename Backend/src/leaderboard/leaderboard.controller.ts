import { Controller, Get, Param } from '@nestjs/common';
import { ApiBearerAuth, ApiOperation, ApiTags } from '@nestjs/swagger';
import { LeaderboardService } from './leaderboard.service';
import { CurrentUser } from '../common/decorators/current-user.decorator';
import { AuthUser } from '../common/types/auth-user';

@ApiTags('leaderboard')
@ApiBearerAuth()
@Controller('contests')
export class LeaderboardController {
  constructor(private readonly leaderboard: LeaderboardService) {}

  @Get(':contestId/leaderboard')
  @ApiOperation({
    summary: 'Contest leaderboard',
    description: 'Poll this endpoint for live ranking. WebSockets are not used.',
  })
  get(@CurrentUser() user: AuthUser, @Param('contestId') contestId: string) {
    return this.leaderboard.get(user, contestId);
  }
}
