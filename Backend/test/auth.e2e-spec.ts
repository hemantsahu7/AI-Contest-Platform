import { INestApplication } from '@nestjs/common';
import request from 'supertest';
import { auth, createTestApp, login, SEED } from './helpers';

describe('auth (e2e)', () => {
  let app: INestApplication;

  beforeAll(async () => {
    app = await createTestApp();
  });

  afterAll(async () => {
    await app.close();
  });

  it('registers, logs in, and returns the current user without passwordHash', async () => {
    const username = `u${Date.now()}`;
    const email = `${username}@example.com`;
    const register = await request(app.getHttpServer())
      .post('/auth/register')
      .send({ username, email, password: 'Password123!' })
      .expect(201);
    expect(register.body.accessToken).toBeDefined();
    expect(register.body.user.passwordHash).toBeUndefined();
    expect(register.body.user.memberships).toEqual([]);

    const token = await login(app, email);
    const me = await request(app.getHttpServer()).get('/auth/me').set(auth(token)).expect(200);
    expect(me.body.email).toBe(email);
    expect(me.body.passwordHash).toBeUndefined();
  });

  it('does not let public registration choose an organization (no self-join)', async () => {
    const username = `sj${Date.now()}`;
    await request(app.getHttpServer())
      .post('/auth/register')
      .send({ username, email: `${username}@example.com`, password: 'Password123!', organizationId: SEED.orgA })
      .expect(400);
    const email = `ok${username}@example.com`;
    const reg = await request(app.getHttpServer())
      .post('/auth/register')
      .send({ username: `ok${username}`, email, password: 'Password123!' })
      .expect(201);
    const token = reg.body.accessToken as string;
    await request(app.getHttpServer()).get(`/contests/${SEED.contestOpen}`).set(auth(token)).expect(403);
    const list = await request(app.getHttpServer()).get('/contests').set(auth(token)).expect(200);
    expect(list.body).toEqual([]);
  });

  it('rejects invalid passwords', async () => {
    await request(app.getHttpServer())
      .post('/auth/login')
      .send({ email: 'learner1@example.com', password: 'wrong-password' })
      .expect(401);
  });

  it('rejects unauthenticated access to protected routes', async () => {
    await request(app.getHttpServer()).get('/contests').expect(401);
  });
});
