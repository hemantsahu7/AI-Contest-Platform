import { INestApplication } from '@nestjs/common';
import request from 'supertest';
import { auth, createTestApp, login, SEED } from './helpers';

const AC_SUM = `#include <iostream>
int main() {
  long long a, b;
  std::cin >> a >> b;
  std::cout << (a + b) << std::endl;
  return 0;
}
`;

const WA_SUM = `#include <iostream>
int main() {
  std::cout << 0 << std::endl;
  return 0;
}
`;

const CE = `int main( { return 0; }`;

const RE = `#include <iostream>
int main() {
  int *p = nullptr;
  std::cout << *p;
  return 0;
}
`;

const TLE = `#include <iostream>
int main() {
  while (true) {}
}
`;

describe('submissions (e2e)', () => {
  let app: INestApplication;
  let learnerToken: string;

  beforeAll(async () => {
    app = await createTestApp();
    learnerToken = await login(app, 'learner1@example.com');
    await request(app.getHttpServer())
      .post(`/contests/${SEED.contestOpen}/join`)
      .set(auth(learnerToken));
  });

  afterAll(async () => {
    await app.close();
  });

  it('queues a submission immediately', async () => {
    const res = await request(app.getHttpServer())
      .post(`/contests/${SEED.contestOpen}/problems/${SEED.problemSum}/submissions`)
      .set(auth(learnerToken))
      .send({ sourceCode: AC_SUM, language: 'cpp' })
      .expect(201);
    expect(res.body.submissionId).toBeDefined();
    expect(res.body.status).toBe('QUEUED');
  });

  it('rejects a problem that does not belong to the contest', async () => {
    await request(app.getHttpServer())
      .post(`/contests/${SEED.contestOpen}/problems/${SEED.endedProblem}/submissions`)
      .set(auth(learnerToken))
      .send({ sourceCode: AC_SUM, language: 'cpp' })
      .expect(400);
  });

  it('rejects submissions after the contest has ended', async () => {
    await request(app.getHttpServer())
      .post(`/contests/${SEED.contestEnded}/problems/${SEED.endedProblem}/submissions`)
      .set(auth(learnerToken))
      .send({ sourceCode: AC_SUM, language: 'cpp' })
      .expect(403);
  });
});

const dockerEnabled = process.env.SKIP_DOCKER !== '1';

(dockerEnabled ? describe : describe.skip)('docker judge (e2e)', () => {
  let app: INestApplication;
  let token: string;

  beforeAll(async () => {
    app = await createTestApp();
    token = await login(app, 'learner1@example.com');
    await request(app.getHttpServer()).post(`/contests/${SEED.contestOpen}/join`).set(auth(token));
  });

  afterAll(async () => {
    await app.close();
  });

  async function submitAndWait(source: string, problemId = SEED.problemSum) {
    const created = await request(app.getHttpServer())
      .post(`/contests/${SEED.contestOpen}/problems/${problemId}/submissions`)
      .set(auth(token))
      .send({ sourceCode: source, language: 'cpp' })
      .expect(201);
    const id = created.body.submissionId as string;
    const deadline = Date.now() + 45000;
    let body: { status: string; verdict: string } = { status: 'QUEUED', verdict: 'PENDING' };
    while (Date.now() < deadline) {
      const res = await request(app.getHttpServer())
        .get(`/submissions/${id}`)
        .set(auth(token))
        .expect(200);
      body = res.body;
      if (body.status === 'COMPLETED' || body.status === 'INFRASTRUCTURE_ERROR') {
        return body;
      }
      await new Promise((r) => setTimeout(r, 1000));
    }
    throw new Error(`Timed out waiting for judge, last status=${body.status}`);
  }

  it('accepts a correct C++ program', async () => {
    const result = await submitAndWait(AC_SUM);
    expect(result.status).toBe('COMPLETED');
    expect(result.verdict).toBe('ACCEPTED');
  }, 60000);

  it('returns WRONG_ANSWER for incorrect output', async () => {
    const result = await submitAndWait(WA_SUM);
    expect(result.verdict).toBe('WRONG_ANSWER');
  }, 60000);

  it('returns COMPILATION_ERROR for invalid C++', async () => {
    const result = await submitAndWait(CE);
    expect(result.verdict).toBe('COMPILATION_ERROR');
  }, 60000);

  it('returns RUNTIME_ERROR or TIME_LIMIT_EXCEEDED for a crashing program', async () => {
    const result = await submitAndWait(RE);
    expect(['RUNTIME_ERROR', 'TIME_LIMIT_EXCEEDED']).toContain(result.verdict);
  }, 60000);

  it('returns TIME_LIMIT_EXCEEDED for an infinite loop', async () => {
    const result = await submitAndWait(TLE, SEED.problemMul);
    expect(result.verdict).toBe('TIME_LIMIT_EXCEEDED');
  }, 60000);
});
