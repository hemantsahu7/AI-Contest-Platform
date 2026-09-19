import { classifyRuntimeSignal } from './runtime-signal';

describe('classifyRuntimeSignal', () => {
  it('maps known crash messages to fixed labels', () => {
    expect(classifyRuntimeSignal('sh: 1: Segmentation fault (core dumped)')).toBe('SEGMENTATION_FAULT');
    expect(classifyRuntimeSignal('Floating point exception (core dumped)')).toBe('FLOATING_POINT_EXCEPTION');
    expect(classifyRuntimeSignal("terminate called after throwing an instance of 'std::out_of_range'")).toBe(
      'ABORTED_OR_UNCAUGHT_EXCEPTION',
    );
  });

  it('never returns raw text, so hidden-test data echoed by the program cannot leak', () => {
    const secret = 'HIDDEN-INPUT-2000000000';
    expect(classifyRuntimeSignal(`${secret} Segmentation fault`)).toBe('SEGMENTATION_FAULT');
    expect(classifyRuntimeSignal(secret)).toBe('NON_ZERO_EXIT');
    expect(classifyRuntimeSignal(null)).toBe('NON_ZERO_EXIT');
  });
});
