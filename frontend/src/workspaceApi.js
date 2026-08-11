const API_TIMEOUT_MS = 20_000;
const API_KEY_STORAGE = 'pm-pal-api-key';

export class ApiError extends Error {
  constructor(message, status = 0, detail = null) {
    super(typeof message === 'string' ? message : message?.message || JSON.stringify(message));
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.code = detail?.code || '';
    this.candidates = detail?.candidates || [];
  }
}

export function getStoredApiKey() {
  return localStorage.getItem(API_KEY_STORAGE) || '';
}

export function setStoredApiKey(value) {
  const trimmed = String(value || '').trim();
  if (trimmed) localStorage.setItem(API_KEY_STORAGE, trimmed);
  else localStorage.removeItem(API_KEY_STORAGE);
  return trimmed;
}

function apiHeaders(extra = {}) {
  const headers = { 'Content-Type': 'application/json', ...extra };
  const apiKey = getStoredApiKey();
  if (apiKey) headers['X-API-Key'] = apiKey;
  return headers;
}

async function request(path, options = {}) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), options.timeoutMs ?? API_TIMEOUT_MS);
  try {
    const response = await fetch(path, {
      ...options,
      headers: apiHeaders(options.headers ?? {}),
      signal: options.signal ?? controller.signal,
    });
    const type = response.headers.get('content-type') ?? '';
    const body = type.includes('application/json') ? await response.json() : await response.text();
    if (!response.ok) {
      const detail = typeof body === 'object' && body !== null ? body.detail : body;
      const message = (typeof detail === 'object' && detail !== null)
        ? (detail.message || JSON.stringify(detail))
        : (detail || `请求失败（${response.status}）`);
      throw new ApiError(message, response.status, typeof detail === 'object' ? detail : null);
    }
    return body;
  } catch (error) {
    if (error.name === 'AbortError') throw new ApiError('请求超时');
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

const query = (params) => new URLSearchParams(Object.entries(params).filter(([, value]) => value !== undefined && value !== null && value !== '')).toString();

export const api = {
  listProjects: () => request('/api/projects'),
  createProject: (name, description = '') => request('/api/projects', { method: 'POST', body: JSON.stringify({ name, description }) }),
  getProject: (projectId) => request(`/api/projects/${encodeURIComponent(projectId)}`),
  lookupProjectByRun: (runId) => request(`/api/projects/by-run/${encodeURIComponent(runId)}`),
  summary: (projectId) => request(`/api/projects/${encodeURIComponent(projectId)}/summary`),
  listEvidence: (projectId, text = '') => request(`/api/projects/${encodeURIComponent(projectId)}/evidence?${query({ query: text, limit: 100 })}`),
  listEvidenceSources: (projectId) => request(`/api/projects/${encodeURIComponent(projectId)}/evidence-sources`),
  createEvidence: (projectId, payload) => request(`/api/projects/${encodeURIComponent(projectId)}/evidence`, {
    method: 'POST',
    body: JSON.stringify({ actor: 'local', ...payload }),
  }),
  confirmEvidence: (projectId, evidenceId, confirmed = true) => request(
    `/api/projects/${encodeURIComponent(projectId)}/evidence/${encodeURIComponent(evidenceId)}/confirm`,
    { method: 'POST', body: JSON.stringify({ confirmed, actor: 'local' }) },
  ),
  addPrdSource: (projectId, { title, content = '', source_url = '', source_type = 'prd_text' }) => request(
    `/api/projects/${encodeURIComponent(projectId)}/sources`,
    { method: 'POST', body: JSON.stringify({ title, content, source_url, source_type, is_prd: true }) },
  ),
  connectPrdSource: (projectId, { source_url, title = '' }) => request(
    `/api/projects/${encodeURIComponent(projectId)}/sources/from-url`,
    { method: 'POST', body: JSON.stringify({ source_url, title, is_prd: true }), timeoutMs: 60_000 },
  ),
  uploadPrdSource: async (projectId, file, { title = '' } = {}) => {
    const form = new FormData();
    form.append('file', file);
    if (title) form.append('title', title);
    form.append('is_prd', 'true');
    const headers = {};
    const apiKey = getStoredApiKey();
    if (apiKey) headers['X-API-Key'] = apiKey;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 60_000);
    try {
      const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/sources/upload`, {
        method: 'POST',
        headers,
        body: form,
        signal: controller.signal,
      });
      const type = response.headers.get('content-type') ?? '';
      const body = type.includes('application/json') ? await response.json() : await response.text();
      if (!response.ok) {
        const detail = typeof body === 'object' && body !== null ? body.detail : body;
        throw new ApiError(detail?.message || detail || `上传失败（${response.status}）`, response.status);
      }
      return body;
    } catch (error) {
      if (error.name === 'AbortError') throw new ApiError('请求超时');
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
  },
  listInsights: (projectId) => request(`/api/projects/${encodeURIComponent(projectId)}/insights`),
  createInsight: (projectId, payload) => request(`/api/projects/${encodeURIComponent(projectId)}/insights`, {
    method: 'POST',
    body: JSON.stringify({ actor: 'local', ...payload }),
  }),
  listOpportunities: (projectId) => request(`/api/projects/${encodeURIComponent(projectId)}/opportunities`),
  createOpportunity: (projectId, payload) => request(`/api/projects/${encodeURIComponent(projectId)}/opportunities`, {
    method: 'POST',
    body: JSON.stringify({ actor: 'local', ...payload }),
  }),
  submitOpportunity: (projectId, opportunityId) => request(
    `/api/projects/${encodeURIComponent(projectId)}/opportunities/${encodeURIComponent(opportunityId)}/submit`,
    { method: 'POST', body: JSON.stringify({ actor: 'local' }) },
  ),
  approveOpportunity: (projectId, opportunityId) => request(
    `/api/projects/${encodeURIComponent(projectId)}/opportunities/${encodeURIComponent(opportunityId)}/approve`,
    { method: 'POST', body: JSON.stringify({ actor: 'local' }) },
  ),
  rejectOpportunity: (projectId, opportunityId) => request(
    `/api/projects/${encodeURIComponent(projectId)}/opportunities/${encodeURIComponent(opportunityId)}/reject`,
    { method: 'POST', body: JSON.stringify({ actor: 'local' }) },
  ),
  listPrds: (projectId) => request(`/api/projects/${encodeURIComponent(projectId)}/prd-versions`),
  createPrd: (projectId, payload) => request(`/api/projects/${encodeURIComponent(projectId)}/prd-versions`, {
    method: 'POST',
    body: JSON.stringify({ actor: 'local', ...payload }),
  }),
  assessPrd: (projectId, prdVersionId) => request(
    `/api/projects/${encodeURIComponent(projectId)}/prd-versions/${encodeURIComponent(prdVersionId)}/assess`,
    { method: 'POST', body: JSON.stringify({ actor: 'local' }) },
  ),
  approvePrd: (projectId, prdVersionId) => request(
    `/api/projects/${encodeURIComponent(projectId)}/prd-versions/${encodeURIComponent(prdVersionId)}/approve`,
    { method: 'POST', body: JSON.stringify({ actor: 'local' }) },
  ),
  waivePrd: (projectId, prdVersionId) => request(
    `/api/projects/${encodeURIComponent(projectId)}/prd-versions/${encodeURIComponent(prdVersionId)}/waive`,
    { method: 'POST', body: JSON.stringify({ actor: 'local', reason: 'owner_waive' }) },
  ),
  readyPrd: (projectId, prdVersionId) => request(
    `/api/projects/${encodeURIComponent(projectId)}/prd-versions/${encodeURIComponent(prdVersionId)}/ready`,
    { method: 'POST', body: JSON.stringify({ actor: 'local' }) },
  ),
  listDeliveries: (projectId) => request(`/api/projects/${encodeURIComponent(projectId)}/deliveries`),
  createDelivery: (projectId, payload) => request(`/api/projects/${encodeURIComponent(projectId)}/deliveries`, {
    method: 'POST',
    body: JSON.stringify({ actor: 'local', target_kind: 'local_bundle', ...payload }),
  }),
  listProviderConnections: () => request('/api/provider-connections'),
  listConversations: (projectId) => request(`/api/agent/conversations?${query({ project_id: projectId, limit: 24 })}`),
  getConversation: (conversationId) => request(`/api/agent/conversations/${encodeURIComponent(conversationId)}`),
  createConversation: (projectId, actor = 'local') => request('/api/agent/conversations', {
    method: 'POST',
    body: JSON.stringify({ project_id: projectId, actor, title: '工作台对话' }),
  }),
  sendMessage: (conversationId, content, actor = 'local', extras = {}) => request(
    `/api/agent/conversations/${encodeURIComponent(conversationId)}/messages`,
    {
      method: 'POST',
      body: JSON.stringify({
        content,
        actor,
        ...(extras.action ? { action: extras.action } : {}),
        ...(extras.source_id ? { source_id: extras.source_id } : {}),
      }),
    },
  ),
  getReviewStatus: (projectId, runId) => request(
    `/api/projects/${encodeURIComponent(projectId)}/reviews/${encodeURIComponent(runId)}`,
  ),
  reviewReportPath: (projectId, runId, format = 'md') => (
    `/api/projects/${encodeURIComponent(projectId)}/reviews/${encodeURIComponent(runId)}/report?format=${encodeURIComponent(format)}`
  ),
  // N+1 is acceptable for small conversation counts; Confirmations is the primary consumer. :-)
  listPendingTasks: async (projectId) => {
    const result = await request(`/api/agent/conversations?${query({ project_id: projectId, limit: 24 })}`);
    const pending = [];
    for (const conversation of result.conversations || []) {
      const detail = await request(`/api/agent/conversations/${encodeURIComponent(conversation.id)}`);
      for (const task of detail.tasks || []) {
        if (task.status === 'awaiting_confirmation') {
          pending.push({ ...task, conversation_id: conversation.id });
        }
      }
    }
    return { items: pending };
  },
  confirmTask: (taskId, actor = 'local') => request(`/api/agent/tasks/${encodeURIComponent(taskId)}/confirm`, {
    method: 'POST',
    body: JSON.stringify({ confirmed: true, actor }),
  }),
  dismissTask: (taskId, actor = 'local') => request(`/api/agent/tasks/${encodeURIComponent(taskId)}/confirm`, {
    method: 'POST',
    body: JSON.stringify({ confirmed: false, actor }),
  }),
};
