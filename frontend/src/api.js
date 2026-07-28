function getErrorMessage(payload, fallbackMessage) {
  if (payload?.detail?.message) {
    return payload.detail.message;
  }
  if (typeof payload?.detail === 'string') {
    return payload.detail;
  }
  if (typeof payload?.message === 'string') {
    return payload.message;
  }
  return fallbackMessage;
}

const defaultRequestTimeoutMs = 15000;
const feishuContextKeys = ['open_id', 'tenant_key', 'embed', 'trigger_source', 'lang', 'locale', 'user_id'];

function getCurrentFeishuContext() {
  if (typeof window === 'undefined' || !window.location) {
    return {};
  }

  const searchParams = new URLSearchParams(window.location.search);
  const context = {};
  feishuContextKeys.forEach((key) => {
    const value = String(searchParams.get(key) ?? '').trim();
    if (value) {
      context[key] = value;
    }
  });
  if (context.open_id || context.tenant_key || context.embed === 'feishu') {
    context.trigger_source = context.trigger_source || 'feishu';
    context.embed = context.embed || 'feishu';
  }
  return context;
}

function projectReviewApiPath(runId, suffix = '') {
  const match = typeof window !== 'undefined'
    ? window.location.pathname.match(/^\/projects\/([^/]+)\/reviews\/[^/]+/)
    : null;
  if (!match) {
    throw new Error('Project review context is required for review API calls.');
  }
  return `/api/projects/${encodeURIComponent(match[1])}/reviews/${encodeURIComponent(runId)}${suffix}`;
}

function appendFeishuContext(path) {
  const context = getCurrentFeishuContext();
  if (!context.open_id && !context.tenant_key && context.embed !== 'feishu') {
    return path;
  }

  const [pathAndQuery, hash = ''] = String(path).split('#');
  const [pathname, query = ''] = pathAndQuery.split('?');
  const params = new URLSearchParams(query);
  Object.entries(context).forEach(([key, value]) => {
    if (value && !params.has(key)) {
      params.set(key, value);
    }
  });
  const queryString = params.toString();
  return `${pathname}${queryString ? `?${queryString}` : ''}${hash ? `#${hash}` : ''}`;
}

function reviewApiPath(runId, suffix = '') {
  return appendFeishuContext(projectReviewApiPath(runId, suffix));
}

function feishuContextHeaders() {
  const context = getCurrentFeishuContext();
  const headers = {};
  if (context.open_id) {
    headers['X-Feishu-Open-Id'] = context.open_id;
  }
  if (context.tenant_key) {
    headers['X-Feishu-Tenant-Key'] = context.tenant_key;
  }
  return headers;
}

async function fetchWithTimeout(path, options = {}) {
  const { timeoutMs = defaultRequestTimeoutMs, headers, ...restOptions } = options;
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => {
    controller.abort();
  }, timeoutMs);

  try {
    return await fetch(path, {
      headers: {
        'Content-Type': 'application/json',
        ...feishuContextHeaders(),
        ...(headers ?? {}),
      },
      ...restOptions,
      signal: controller.signal,
    });
  } catch (error) {
    if (error?.name === 'AbortError') {
      const timeoutError = new Error(`Request timed out after ${timeoutMs}ms.`);
      timeoutError.name = 'TimeoutError';
      throw timeoutError;
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

async function requestJson(path, options = {}) {
  const response = await fetchWithTimeout(path, options);

  const contentType = response.headers.get('content-type') ?? '';
  const payload = contentType.includes('application/json')
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const error = new Error(getErrorMessage(payload, `Request failed with status ${response.status}.`));
    error.status = response.status;
    error.payload = payload;
    throw error;
  }

  return payload;
}

function parseFilename(response, fallback) {
  const disposition = response.headers.get('content-disposition') ?? '';
  const match = disposition.match(/filename="?([^";]+)"?/i);
  return match?.[1] ?? fallback;
}

export function fetchReviewStatus(runId) {
  return requestJson(reviewApiPath(runId));
}

export function fetchReviewResult(runId) {
  return requestJson(reviewApiPath(runId, '/result'));
}

export function answerReviewClarification(runId, payload) {
  return requestJson(reviewApiPath(runId, '/clarification'), {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function updateReviewRevisionStage(runId, payload) {
  return requestJson(reviewApiPath(runId, '/revision-stage'), {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function submitReviewRevisionInput(runId, payload) {
  return requestJson(reviewApiPath(runId, '/revision-input'), {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function confirmReviewRevision(runId, payload) {
  return requestJson(reviewApiPath(runId, '/revision-confirm'), {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function fetchArtifactPreview(runId, artifactKey) {
  return requestJson(reviewApiPath(runId, `/artifacts/${encodeURIComponent(artifactKey)}`));
}

export function generateReviewRoadmap(runId) {
  return requestJson(reviewApiPath(runId, '/roadmap-generate'), {
    method: 'POST',
    body: JSON.stringify({}),
  });
}

export function buildReviewProgressStreamUrl(runId) {
  return reviewApiPath(runId, '/progress/stream');
}

export function createPmFeedback(payload) {
  return requestJson('/api/pm/feedback', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function listPmProducts() {
  return requestJson('/api/pm/products');
}

export function createPmProduct(payload) {
  return requestJson('/api/pm/products', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function fetchPmWorkspace(productId) {
  return requestJson(`/api/pm/products/${encodeURIComponent(productId)}/workspace`);
}

export function runPmPipeline(payload) {
  return requestJson('/api/pm/pipeline/run', {
    method: 'POST',
    body: JSON.stringify(payload),
    timeoutMs: 120000,
  });
}

export function fetchPmPipeline(pipelineId) {
  return requestJson(`/api/pm/pipeline/${encodeURIComponent(pipelineId)}`);
}

export function listDecisionSources(productId = '') {
  const params = new URLSearchParams();
  if (productId) params.set('product_id', productId);
  const query = params.toString();
  return requestJson(`/api/decision/sources${query ? `?${query}` : ''}`);
}

export function listDecisionEvidence({ productId = '', query = '', limit = 100 } = {}) {
  const params = new URLSearchParams();
  if (productId) params.set('product_id', productId);
  if (query) params.set('query', query);
  params.set('limit', String(limit));
  return requestJson(`/api/decision/evidence?${params.toString()}`);
}

export function refreshDecisionSource(sourceId) {
  return requestJson(`/api/decision/sources/${encodeURIComponent(sourceId)}/refresh`, {
    method: 'POST',
    body: '{}',
  });
}

export function getDecisionSyncStatus(sourceId) {
  return requestJson(`/api/decision/sources/${encodeURIComponent(sourceId)}/sync-status`);
}

export function listDecisionInsights(productId = '') {
  const params = new URLSearchParams();
  if (productId) params.set('product_id', productId);
  const query = params.toString();
  return requestJson(`/api/decision/insights${query ? `?${query}` : ''}`);
}

export function listDecisionOpportunities(productId = '') {
  const params = new URLSearchParams();
  if (productId) params.set('product_id', productId);
  const query = params.toString();
  return requestJson(`/api/decision/opportunities${query ? `?${query}` : ''}`);
}

export function submitDecisionOpportunity(opportunityId, payload) {
  return requestJson(`/api/decision/opportunities/${encodeURIComponent(opportunityId)}/submit`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function approveDecisionOpportunity(opportunityId, payload) {
  return requestJson(`/api/decision/opportunities/${encodeURIComponent(opportunityId)}/approve`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function rejectDecisionOpportunity(opportunityId, payload) {
  return requestJson(`/api/decision/opportunities/${encodeURIComponent(opportunityId)}/reject`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function listDecisionPrdVersions(productId = '') {
  const params = new URLSearchParams();
  if (productId) params.set('product_id', productId);
  const query = params.toString();
  return requestJson(`/api/decision/prd-versions${query ? `?${query}` : ''}`);
}

export function assessDecisionPrd(prdVersionId, payload = {}) {
  return requestJson(`/api/decision/prd-versions/${encodeURIComponent(prdVersionId)}/assess`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function approveDecisionPrd(prdVersionId, payload) {
  return requestJson(`/api/decision/prd-versions/${encodeURIComponent(prdVersionId)}/approve`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function waiveDecisionPrd(prdVersionId, payload) {
  return requestJson(`/api/decision/prd-versions/${encodeURIComponent(prdVersionId)}/waive`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function readyDecisionPrd(prdVersionId, payload) {
  return requestJson(`/api/decision/prd-versions/${encodeURIComponent(prdVersionId)}/ready`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function exportDecisionPrd(prdVersionId, payload) {
  return requestJson(`/api/decision/prd-versions/${encodeURIComponent(prdVersionId)}/export`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function listDecisionDeliveries({ productId = '', prdVersionId = '' } = {}) {
  const params = new URLSearchParams();
  if (productId) params.set('product_id', productId);
  if (prdVersionId) params.set('prd_version_id', prdVersionId);
  const query = params.toString();
  return requestJson(`/api/decision/deliveries${query ? `?${query}` : ''}`);
}

export function fetchDecisionTrace(rootId) {
  return requestJson(`/api/decision/trace/${encodeURIComponent(rootId)}`);
}

export function fetchPilotMetrics(productId = '') {
  const params = new URLSearchParams();
  if (productId) params.set('product_id', productId);
  const query = params.toString();
  return requestJson(`/api/decision/pilot/metrics${query ? `?${query}` : ''}`);
}

export async function downloadReportArtifact(runId, format) {
  const projectMatch = typeof window !== 'undefined'
    ? window.location.pathname.match(/^\/projects\/([^/]+)\/reviews\/[^/]+/)
    : null;
  if (!projectMatch) {
    throw new Error('Project review context is required to download report artifacts.');
  }
  const reportPath = `/api/projects/${encodeURIComponent(projectMatch[1])}/reviews/${encodeURIComponent(runId)}/report?format=${encodeURIComponent(format)}`;
  const response = await fetchWithTimeout(appendFeishuContext(reportPath), {
    timeoutMs: 20000,
    headers: {},
  });
  const fallbackNameByFormat = {
    md: 'report.md',
    json: 'report.json',
    html: 'report.html',
    csv: 'report.csv',
  };
  const fallbackName = fallbackNameByFormat[format] ?? 'report.md';

  if (!response.ok) {
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    const error = new Error(getErrorMessage(payload, `Download failed with status ${response.status}.`));
    error.status = response.status;
    error.payload = payload;
    throw error;
  }

  const blob = await response.blob();
  const url = window.URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = parseFilename(response, fallbackName);
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.URL.revokeObjectURL(url);
}
export function lookupProjectByRun(runId) {
  return requestJson(`/api/projects/by-run/${encodeURIComponent(runId)}`);
}
export function listProjects() { return requestJson('/api/projects'); }
export function getProject(projectId) { return requestJson(`/api/projects/${encodeURIComponent(projectId)}`); }
export function createProject(payload) { return requestJson('/api/projects', { method: 'POST', body: JSON.stringify(payload) }); }
export function addProjectSource(projectId, payload) { return requestJson(`/api/projects/${encodeURIComponent(projectId)}/sources`, { method: 'POST', body: JSON.stringify(payload) }); }
export function createProjectReview(projectId, payload) { return requestJson(`/api/projects/${encodeURIComponent(projectId)}/reviews`, { method: 'POST', body: JSON.stringify(payload) }); }
export function getProjectTimeline(projectId) { return requestJson(`/api/projects/${encodeURIComponent(projectId)}/timeline`); }
export function fetchProviderCatalog() { return requestJson('/api/provider-catalog'); }
export function listProviderConnections() { return requestJson('/api/provider-connections'); }
export function createProviderConnection(payload) { return requestJson('/api/provider-connections', { method: 'POST', body: JSON.stringify(payload) }); }
export function testProviderConnection(connectionId) { return requestJson(`/api/provider-connections/${encodeURIComponent(connectionId)}/test`, { method: 'POST', body: '{}' }); }

