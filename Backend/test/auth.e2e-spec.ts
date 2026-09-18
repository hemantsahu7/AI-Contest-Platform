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
      .send({
        username,
        email,
        password: 'Password123!',
        organizationId: SEED.orgA,
      })
      .expect(201);
    expect(register.body.accessToken).toBeDefined();
    expect(register.body.user.passwordHash).toBeUndefined();

    const token = await login(app, email);
    const me = await request(app.getHttpServer()).get('/auth/me').set(auth(token)).expect(200);
    expect(me.body.email).toBe(email);
    expect(me.body.passwordHash).toBeUndefined();
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
