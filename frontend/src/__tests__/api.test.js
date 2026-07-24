import { beforeEach, describe, expect, it } from 'vitest';
import { buildReviewProgressStreamUrl, fetchReviewResult, runPmPipeline } from '../api';

const okJsonResponse = {
  ok: true,
  status: 200,
  headers: {
    get: () => 'application/json',
  },
  json: async () => ({ ok: true, pipeline_id: 'pipe-1' }),
  text: async () => '',
};

describe('api Feishu context propagation', () => {
  beforeEach(() => {
    window.history.replaceState(
      {},
      '',
      '/run/20260409T120001Z?embed=feishu&open_id=ou_owner&tenant_key=tenant-a&locale=zh-CN',
    );
    window.fetch.mockResolvedValue(okJsonResponse);
  });

  it('adds explicit Feishu context query params and headers to run-level requests', async () => {
    await fetchReviewResult('20260409T120001Z');

    const [url, options] = window.fetch.mock.calls[0];
    expect(url).toBe(
      '/api/review/20260409T120001Z/result?open_id=ou_owner&tenant_key=tenant-a&embed=feishu&locale=zh-CN&trigger_source=feishu',
    );
    expect(options.headers).toMatchObject({
      'Content-Type': 'application/json',
      'X-Feishu-Open-Id': 'ou_owner',
      'X-Feishu-Tenant-Key': 'tenant-a',
    });
  });

  it('builds the SSE progress URL with the same explicit context', () => {
    expect(buildReviewProgressStreamUrl('20260409T120001Z')).toBe(
      '/api/review/20260409T120001Z/progress/stream?open_id=ou_owner&tenant_key=tenant-a&embed=feishu&locale=zh-CN&trigger_source=feishu',
    );
  });
});

describe('api PM endpoints', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/pm');
    window.fetch.mockResolvedValue(okJsonResponse);
  });

  it('posts pipeline run requests to /api/pm/pipeline/run', async () => {
    await runPmPipeline({
      feedback_texts: ['Login is confusing'],
      product_hint: 'commerce',
      run_quality_gate: false,
    });

    const [url, options] = window.fetch.mock.calls[0];
    expect(url).toBe('/api/pm/pipeline/run');
    expect(options.method).toBe('POST');
    expect(JSON.parse(options.body)).toMatchObject({
      feedback_texts: ['Login is confusing'],
      product_hint: 'commerce',
      run_quality_gate: false,
    });
  });
});
