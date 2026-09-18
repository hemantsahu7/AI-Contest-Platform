import { Module } from '@nestjs/common';
import { ConfigModule, ConfigService } from '@nestjs/config';
import { BullModule } from '@nestjs/bullmq';

function redisConnection(url: string) {
  const parsed = new URL(url);
  return {
    host: parsed.hostname,
    port: Number(parsed.port || 6379),
    username: parsed.username || undefined,
    password: parsed.password || undefined,
  };
}

export const SUBMISSION_QUEUE = 'submissions';

@Module({
  imports: [
    BullModule.forRootAsync({
      imports: [ConfigModule],
      inject: [ConfigService],
      useFactory: (config: ConfigService) => ({
        connection: redisConnection(config.get<string>('REDIS_URL', 'redis://localhost:6379')),
      }),
    }),
    BullModule.registerQueue({ name: SUBMISSION_QUEUE }),
  ],
  exports: [BullModule],
})
export class QueueModule {}
