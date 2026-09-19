export type RuntimeSignal =
  | 'SEGMENTATION_FAULT'
  | 'FLOATING_POINT_EXCEPTION'
  | 'ABORTED_OR_UNCAUGHT_EXCEPTION'
  | 'KILLED'
  | 'NON_ZERO_EXIT';

const SIGNALS: [RegExp, RuntimeSignal][] = [
  [/segmentation fault/i, 'SEGMENTATION_FAULT'],
  [/floating point exception/i, 'FLOATING_POINT_EXCEPTION'],
  [/aborted|terminate called/i, 'ABORTED_OR_UNCAUGHT_EXCEPTION'],
  [/killed/i, 'KILLED'],
];

/**
 * Maps the raw stderr of a crashed test run to a fixed label. Raw runtime output is never shown to learners
 * (it can contain hidden-test data the program echoed), but the crash *kind* is safe and useful for debugging.
 */
export function classifyRuntimeSignal(stderr: string | null | undefined): RuntimeSignal {
  const text = stderr ?? '';
  for (const [pattern, signal] of SIGNALS) {
    if (pattern.test(text)) {
      return signal;
    }
  }
  return 'NON_ZERO_EXIT';
}
