import { Injectable, Logger } from '@nestjs/common';
import { InjectQueue } from '@nestjs/bullmq';
import { Queue } from 'bullmq';
import { SUBMISSION_QUEUE } from '../queue/queue.module';

@Injectable()
export class JudgeService {
  private readonly logger = new Logger(JudgeService.name);

  constructor(@InjectQueue(SUBMISSION_QUEUE) private readonly queue: Queue) {}

  async enqueue(submissionId: string) {
    this.logger.log(`Enqueue judge job for submission ${submissionId}`);
    try {
      await this.queue.add(
        'judge',
        { submissionId },
        {
          jobId: submissionId,
          attempts: 3,
          backoff: { type: 'exponential', delay: 2000 },
          removeOnComplete: 100,
          removeOnFail: 50,
        },
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      if (message.toLowerCase().includes('already exists')) {
        this.logger.log(`Judge job already queued for ${submissionId}`);
        return;
      }
      throw error;
    }
  }
}
