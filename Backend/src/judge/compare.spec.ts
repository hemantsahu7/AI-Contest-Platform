import { normalizeOutput, outputsMatch } from './compare';

describe('output comparison', () => {
  it('normalizes line endings and trailing whitespace', () => {
    expect(normalizeOutput('1 \r\n2\r\n')).toBe('1\n2');
    expect(outputsMatch('3\n', '3')).toBe(true);
    expect(outputsMatch('3', '4')).toBe(false);
  });
});
