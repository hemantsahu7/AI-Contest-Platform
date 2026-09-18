import { INestApplication, ValidationPipe } from '@nestjs/common';
import { Test } from '@nestjs/testing';
import request from 'supertest';
import { AppModule } from '../src/app.module';
import { HttpErrorFilter } from '../src/common/filters/http-error.filter';

export const SEED = {
  orgA: '11111111-1111-4111-8111-111111111111',
  orgB: '11111111-1111-4111-8111-111111111112',
  contestOpen: '33333333-3333-4333-8333-333333333331',
  contestEnded: '33333333-3333-4333-8333-333333333332',
  contestB: '33333333-3333-4333-8333-333333333333',
  problemSum: '44444444-4444-4444-8444-444444444441',
  problemMax: '44444444-4444-4444-8444-444444444442',
  problemMul: '44444444-4444-4444-8444-444444444444',
  endedProblem: '44444444-4444-4444-8444-444444444449',
  learner2Submission: '66666666-6666-4666-8666-666666666663',
  password: 'Password123!',
};

export async function createTestApp(): Promise<INestApplication> {
  const moduleRef = await Test.createTestingModule({
    imports: [AppModule],
  }).compile();
  const app = moduleRef.createNestApplication();
  app.useGlobalFilters(new HttpErrorFilter());
  app.useGlobalPipes(
    new ValidationPipe({
      whitelist: true,
      forbidNonWhitelisted: true,
      transform: true,
    }),
  );
  await app.init();
  return app;
}

export async function login(app: INestApplication, email: string) {
  const res = await request(app.getHttpServer())
    .post('/auth/login')
    .send({ email, password: SEED.password })
    .expect(201);
  return res.body.accessToken as string;
}

export function auth(token: string) {
  return { Authorization: `Bearer ${token}` };
}
