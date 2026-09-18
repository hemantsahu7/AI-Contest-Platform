import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { IncidentType } from '@prisma/client';
import Docker from 'dockerode';
import { PassThrough } from 'stream';
import * as tar from 'tar-stream';
import { outputsMatch } from './compare';

export type JudgeTestCase = {
  input: string;
  expectedOutput: string;
};

export type JudgeRunResult =
  | {
      kind: 'student';
      verdict:
        | 'COMPILATION_ERROR'
        | 'RUNTIME_ERROR'
        | 'TIME_LIMIT_EXCEEDED'
        | 'WRONG_ANSWER'
        | 'ACCEPTED';
      stdout: string;
      stderr: string;
      testsPassed: number;
      testsTotal: number;
      executionTimeMs: number;
      memoryUsedMb: number;
    }
  | {
      kind: 'infrastructure';
      type: IncidentType;
      message: string;
    };

@Injectable()
export class DockerRunnerService {
  private readonly logger = new Logger(DockerRunnerService.name);
  private readonly docker: Docker;
  private readonly image: string;

  constructor(private readonly config: ConfigService) {
    this.docker = new Docker();
    this.image = this.config.get<string>('JUDGE_IMAGE', 'shodh-judge:v1');
  }

  async runCpp(params: {
    sourceCode: string;
    timeLimitMs: number;
    memoryLimitMb: number;
    tests: JudgeTestCase[];
  }): Promise<JudgeRunResult> {
    const testsTotal = params.tests.length;
    let container: Docker.Container | undefined;
    const wallStart = Date.now();

    try {
      container = await this.docker.createContainer({
        Image: this.image,
        WorkingDir: '/workspace',
        User: '0',
        NetworkDisabled: true,
        HostConfig: {
          Memory: params.memoryLimitMb * 1024 * 1024,
          MemorySwap: params.memoryLimitMb * 1024 * 1024,
          NanoCpus: 1_000_000_000,
          NetworkMode: 'none',
          AutoRemove: false,
          PidsLimit: 64,
          ReadonlyRootfs: false,
        },
        Cmd: ['sleep', '120'],
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.logger.error(`Failed to create judge container: ${message}`);
      return {
        kind: 'infrastructure',
        type: IncidentType.CONTAINER_START_FAILURE,
        message,
      };
    }

    try {
      await container.start();
      await this.putFiles(container, { 'source.cpp': params.sourceCode });
      await this.exec(container, ['chmod', '-R', 'a+rwx', '/workspace'], 5000);

      const compile = await this.exec(
        container,
        ['g++', '-O2', '-std=c++17', '-o', '/workspace/program', '/workspace/source.cpp'],
        15000,
        'judge',
      );
      if (compile.timedOut) {
        return {
          kind: 'student',
          verdict: 'TIME_LIMIT_EXCEEDED',
          stdout: compile.stdout,
          stderr: compile.stderr,
          testsPassed: 0,
          testsTotal,
          executionTimeMs: Date.now() - wallStart,
          memoryUsedMb: params.memoryLimitMb,
        };
      }
      if (compile.exitCode !== 0) {
        return {
          kind: 'student',
          verdict: 'COMPILATION_ERROR',
          stdout: compile.stdout,
          stderr: compile.stderr,
          testsPassed: 0,
          testsTotal,
          executionTimeMs: Date.now() - wallStart,
          memoryUsedMb: params.memoryLimitMb,
        };
      }

      let testsPassed = 0;
      let lastStdout = '';
      let lastStderr = '';
      const perTestSeconds = Math.max(1, Math.ceil(params.timeLimitMs / 1000));

      for (const test of params.tests) {
        await this.putFiles(container, { 'input.txt': test.input });
        const run = await this.exec(
          container,
          [
            'sh',
            '-c',
            `timeout --signal=KILL ${perTestSeconds}s /workspace/program < /workspace/input.txt`,
          ],
          params.timeLimitMs + 2000,
          'judge',
        );
        lastStdout = run.stdout;
        lastStderr = run.stderr;

        if (run.timedOut || run.exitCode === 124 || run.exitCode === 137) {
          return {
            kind: 'student',
            verdict: 'TIME_LIMIT_EXCEEDED',
            stdout: lastStdout,
            stderr: lastStderr,
            testsPassed,
            testsTotal,
            executionTimeMs: Date.now() - wallStart,
            memoryUsedMb: params.memoryLimitMb,
          };
        }
        if (run.exitCode !== 0) {
          return {
            kind: 'student',
            verdict: 'RUNTIME_ERROR',
            stdout: lastStdout,
            stderr: lastStderr,
            testsPassed,
            testsTotal,
            executionTimeMs: Date.now() - wallStart,
            memoryUsedMb: params.memoryLimitMb,
          };
        }
        if (!outputsMatch(run.stdout, test.expectedOutput)) {
          return {
            kind: 'student',
            verdict: 'WRONG_ANSWER',
            stdout: lastStdout,
            stderr: lastStderr,
            testsPassed,
            testsTotal,
            executionTimeMs: Date.now() - wallStart,
            memoryUsedMb: params.memoryLimitMb,
          };
        }
        testsPassed += 1;
      }

      return {
        kind: 'student',
        verdict: 'ACCEPTED',
        stdout: lastStdout,
        stderr: lastStderr,
        testsPassed,
        testsTotal,
        executionTimeMs: Date.now() - wallStart,
        memoryUsedMb: params.memoryLimitMb,
      };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.logger.error(`Docker judge error: ${message}`);
      return {
        kind: 'infrastructure',
        type: IncidentType.DOCKER_ERROR,
        message,
      };
    } finally {
      await this.cleanup(container);
    }
  }

  private async putFiles(container: Docker.Container, files: Record<string, string>) {
    const pack = tar.pack();
    const packed = new Promise<Buffer>((resolve, reject) => {
      const chunks: Buffer[] = [];
      pack.on('data', (chunk) => chunks.push(chunk as Buffer));
      pack.on('end', () => resolve(Buffer.concat(chunks)));
      pack.on('error', reject);
    });
    for (const [name, content] of Object.entries(files)) {
      pack.entry({ name, mode: 0o644 }, content);
    }
    pack.finalize();
    const archive = await packed;
    await new Promise<void>((resolve, reject) => {
      container.putArchive(archive, { path: '/workspace' }, (err) => {
        if (err) reject(err);
        else resolve();
      });
    });
  }

  private async exec(
    container: Docker.Container,
    cmd: string[],
    timeoutMs: number,
    user = '0',
  ): Promise<{ exitCode: number; stdout: string; stderr: string; timedOut: boolean }> {
    const exec = await container.exec({
      Cmd: cmd,
      AttachStdout: true,
      AttachStderr: true,
      User: user,
      WorkingDir: '/workspace',
    });
    const stream = await exec.start({ hijack: true, stdin: false });
    const stdout = new PassThrough();
    const stderr = new PassThrough();
    container.modem.demuxStream(stream, stdout, stderr);
    const stdoutChunks: Buffer[] = [];
    const stderrChunks: Buffer[] = [];
    stdout.on('data', (c) => stdoutChunks.push(c as Buffer));
    stderr.on('data', (c) => stderrChunks.push(c as Buffer));

    let timedOut = false;
    const timeout = setTimeout(() => {
      timedOut = true;
      void container.kill().catch(() => undefined);
    }, timeoutMs);

    await new Promise<void>((resolve, reject) => {
      stream.on('end', () => resolve());
      stream.on('error', reject);
    });
    clearTimeout(timeout);

    const info = await exec.inspect();
    return {
      exitCode: info.ExitCode ?? 1,
      stdout: Buffer.concat(stdoutChunks).toString('utf8').slice(0, 8000),
      stderr: Buffer.concat(stderrChunks).toString('utf8').slice(0, 8000),
      timedOut,
    };
  }

  private async cleanup(container?: Docker.Container) {
    if (!container) {
      return;
    }
    try {
      await container.stop({ t: 1 });
    } catch {
      // already stopped
    }
    try {
      await container.remove({ force: true });
    } catch (error) {
      this.logger.warn(
        `Failed to remove judge container: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
  }
}
