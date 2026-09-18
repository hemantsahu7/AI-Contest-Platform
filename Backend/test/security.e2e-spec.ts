import { INestApplication } from '@nestjs/common';
import request from 'supertest';
import { auth, createTestApp, login, SEED } from './helpers';

describe('security (e2e)', () => {
  let app: INestApplication;
  let learnerToken: string;
  let learnerBToken: string;
  let instructorToken: string;

  beforeAll(async () => {
    app = await createTestApp();
    learnerToken = await login(app, 'learner1@example.com');
    learnerBToken = await login(app, 'learner-b@example.com');
    instructorToken = await login(app, 'instructor@example.com');
  });

  afterAll(async () => {
    await app.close();
  });

  it('does not let a learner create a contest', async () => {
    await request(app.getHttpServer())
      .post('/contests')
      .set(auth(learnerToken))
      .send({
        title: 'Nope',
        description: 'Learner cannot create',
        organizationId: SEED.orgA,
        startTime: '2026-01-01T00:00:00.000Z',
        endTime: '2026-02-01T00:00:00.000Z',
      })
      .expect(403);
  });

  it('does not let a learner modify a problem', async () => {
    await request(app.getHttpServer())
      .patch(`/contests/${SEED.contestOpen}/problems/${SEED.problemSum}`)
      .set(auth(learnerToken))
      .send({ title: 'Hacked' })
      .expect(403);
  });

  it('denies cross-organization contest access', async () => {
    await request(app.getHttpServer())
      .get(`/contests/${SEED.contestB}`)
      .set(auth(learnerToken))
      .expect(403);
    await request(app.getHttpServer())
      .get(`/contests/${SEED.contestOpen}`)
      .set(auth(learnerBToken))
      .expect(403);
  });

  it('hides hidden test cases from learners', async () => {
    const res = await request(app.getHttpServer())
      .get(`/contests/${SEED.contestOpen}/problems/${SEED.problemSum}`)
      .set(auth(learnerToken))
      .expect(200);
    expect(res.body.testCases.every((t: { isHidden: boolean }) => t.isHidden === false)).toBe(
      true,
    );
    const instructorView = await request(app.getHttpServer())
      .get(`/contests/${SEED.contestOpen}/problems/${SEED.problemSum}`)
      .set(auth(instructorToken))
      .expect(200);
    expect(instructorView.body.testCases.some((t: { isHidden: boolean }) => t.isHidden)).toBe(
      true,
    );
  });

  it('does not return another learner source code', async () => {
    const other = await request(app.getHttpServer())
      .get(`/submissions/${SEED.learner2Submission}`)
      .set(auth(learnerToken))
      .expect(404);
    expect(other.body.message).toBeDefined();
  });
});
