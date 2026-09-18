import { Module } from '@nestjs/common';
import { SubmissionsService } from './submissions.service';
import { SubmissionsController } from './submissions.controller';
import { ContestsModule } from '../contests/contests.module';
import { JudgeModule } from '../judge/judge.module';

@Module({
  imports: [ContestsModule, JudgeModule],
  controllers: [SubmissionsController],
  providers: [SubmissionsService],
})
export class SubmissionsModule {}
