import { Body, Controller, Get, Param, Post } from '@nestjs/common';
import { ApiBearerAuth, ApiTags } from '@nestjs/swagger';
import { SubmissionsService } from './submissions.service';
import { CreateSubmissionDto } from './dto/create-submission.dto';
import { CurrentUser } from '../common/decorators/current-user.decorator';
import { AuthUser } from '../common/types/auth-user';

@ApiTags('submissions')
@ApiBearerAuth()
@Controller()
export class SubmissionsController {
  constructor(private readonly submissions: SubmissionsService) {}

  @Post('contests/:contestId/problems/:problemId/submissions')
  create(
    @CurrentUser() user: AuthUser,
    @Param('contestId') contestId: string,
    @Param('problemId') problemId: string,
    @Body() dto: CreateSubmissionDto,
  ) {
    return this.submissions.create(user, contestId, problemId, dto);
  }

  @Get('submissions/:submissionId')
  getById(@CurrentUser() user: AuthUser, @Param('submissionId') submissionId: string) {
    return this.submissions.getById(user, submissionId);
  }

  @Get('contests/:contestId/submissions')
  list(@CurrentUser() user: AuthUser, @Param('contestId') contestId: string) {
    return this.submissions.listForContest(user, contestId);
  }
}
