import { INestApplication } from '@nestjs/common';
import request from 'supertest';
import { auth, createTestApp, login, SEED } from './helpers';

describe('contests (e2e)', () => {
  let app: INestApplication;
  let instructorToken: string;
  let learnerToken: string;

  beforeAll(async () => {
    app = await createTestApp();
    instructorToken = await login(app, 'instructor@example.com');
    learnerToken = await login(app, 'learner1@example.com');
  });

  afterAll(async () => {
    await app.close();
  });

  it('lets an instructor create a contest and rejects invalid dates', async () => {
    const created = await request(app.getHttpServer())
      .post('/contests')
      .set(auth(instructorToken))
      .send({
        title: 'Instructor Contest',
        description: 'Created in tests',
        organizationId: SEED.orgA,
        startTime: '2026-01-01T00:00:00.000Z',
        endTime: '2026-02-01T00:00:00.000Z',
        status: 'UPCOMING',
      })
      .expect(201);
    expect(created.body.id).toBeDefined();

    await request(app.getHttpServer())
      .post('/contests')
      .set(auth(instructorToken))
      .send({
        title: 'Bad dates',
        description: 'end before start',
        organizationId: SEED.orgA,
        startTime: '2026-02-01T00:00:00.000Z',
        endTime: '2026-01-01T00:00:00.000Z',
      })
      .expect(400);
  });

  it('lets an instructor create a problem that learners can view', async () => {
    const created = await request(app.getHttpServer())
      .post(`/contests/${SEED.contestOpen}/problems`)
      .set(auth(instructorToken))
      .send({
        title: 'Echo Zero',
        description: 'Print 0',
        difficulty: 'EASY',
        points: 10,
        timeLimitMs: 1000,
        memoryLimitMb: 64,
        inputFormat: 'none',
        outputFormat: '0',
      })
      .expect(201);
    const viewed = await request(app.getHttpServer())
      .get(`/contests/${SEED.contestOpen}/problems/${created.body.id}`)
      .set(auth(learnerToken))
      .expect(200);
    expect(viewed.body.title).toBe('Echo Zero');
    expect(viewed.body.testCases).toEqual([]);
  });

  it('joins a running contest and prevents duplicate joins', async () => {
    const learner2 = await login(app, 'learner2@example.com');
    await request(app.getHttpServer())
      .post(`/contests/${SEED.contestOpen}/join`)
      .set(auth(learner2))
      .expect((res) => {
        if (![201, 409].includes(res.status)) {
          throw new Error(`unexpected join status ${res.status}`);
        }
      });
    await request(app.getHttpServer())
      .post(`/contests/${SEED.contestOpen}/join`)
      .set(auth(learner2))
      .expect(409);
  });

  it('lists participants', async () => {
    const res = await request(app.getHttpServer())
      .get(`/contests/${SEED.contestOpen}/participants`)
      .set(auth(learnerToken))
      .expect(200);
    expect(Array.isArray(res.body)).toBe(true);
  });
});
