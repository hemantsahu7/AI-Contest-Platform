import { Module } from '@nestjs/common';
import { QueueModule } from '../queue/queue.module';
import { JudgeService } from './judge.service';
import { JudgeProcessor } from './judge.processor';
import { DockerRunnerService } from './docker-runner.service';

@Module({
  imports: [QueueModule],
  providers: [JudgeService, JudgeProcessor, DockerRunnerService],
  exports: [JudgeService],
})
export class JudgeModule {}
