import { INestApplication } from '@nestjs/common';
import request from 'supertest';
import { auth, createTestApp, login, SEED } from './helpers';

describe('leaderboard (e2e)', () => {
  let app: INestApplication;
  let token: string;

  beforeAll(async () => {
    app = await createTestApp();
    token = await login(app, 'learner1@example.com');
  });

  afterAll(async () => {
    await app.close();
  });

  it('returns deterministic ranks with score and solved counts', async () => {
    const res = await request(app.getHttpServer())
      .get(`/contests/${SEED.contestOpen}/leaderboard`)
      .set(auth(token))
      .expect(200);
    expect(Array.isArray(res.body)).toBe(true);
    expect(res.body[0].rank).toBe(1);
    expect(res.body[0].score).toBeGreaterThanOrEqual(res.body[1]?.score ?? 0);
    const learner1 = res.body.find((r: { username: string }) => r.username === 'learner1');
    expect(learner1.score).toBeGreaterThanOrEqual(200);
    expect(learner1.solved).toBeGreaterThanOrEqual(2);
  });
});
