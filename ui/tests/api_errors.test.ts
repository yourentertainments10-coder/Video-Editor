import { describe, expect, it } from 'vitest';
import { API_LEVEL, apiLevelProblem, errorMessage } from '../src/api';

describe('errorMessage', () => {
  it('shows the FastAPI detail instead of raw JSON', () => {
    expect(errorMessage(422, '{"detail":"text must be a string"}')).toBe('text must be a string (422)');
    expect(errorMessage(409, 'timeline revision conflict')).toBe('timeline revision conflict (409)');
  });
  it('explains a missing route as an old server that needs a restart', () => {
    expect(errorMessage(404, '{"detail":"Not Found"}')).toMatch(/start it again/);
  });
  it('explains a lost token', () => {
    expect(errorMessage(401, 'Unauthorized')).toMatch(/Reload the page/);
  });
  it('joins validation lists', () => {
    expect(errorMessage(422, '{"detail":[{"msg":"a"},{"msg":"b"}]}')).toBe('a; b (422)');
  });
});

describe('apiLevelProblem', () => {
  it('is quiet when page and server match', () => { expect(apiLevelProblem(API_LEVEL)).toBeNull(); });
  it('asks to restart an older server (also one without api_level)', () => {
    expect(apiLevelProblem(undefined)).toMatch(/older than this page/);
    expect(apiLevelProblem(API_LEVEL - 1)).toMatch(/start it again/);
  });
  it('asks to reload an older page', () => { expect(apiLevelProblem(API_LEVEL + 1)).toMatch(/Reload the page/); });
});
