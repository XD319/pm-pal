import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrowserRouter } from 'react-router-dom';

vi.mock('../workspaceApi', () => ({
  api: {
    listProjects: vi.fn(),
    summary: vi.fn(),
    listConversations: vi.fn(),
    getConversation: vi.fn(),
    listPendingTasks: vi.fn(),
    getProject: vi.fn(),
    listEvidence: vi.fn(),
    listInsights: vi.fn(),
    listOpportunities: vi.fn(),
    listPrds: vi.fn(),
    listDeliveries: vi.fn(),
    listProviderConnections: vi.fn(),
    lookupProjectByRun: vi.fn(),
    createConversation: vi.fn(),
    sendMessage: vi.fn(),
    getReviewStatus: vi.fn(),
    reviewReportPath: vi.fn((projectId, runId, format = 'md') => `/api/projects/${projectId}/reviews/${runId}/report?format=${format}`),
    createInsight: vi.fn(),
    createOpportunity: vi.fn(),
    submitOpportunity: vi.fn(),
    approveOpportunity: vi.fn(),
    rejectOpportunity: vi.fn(),
    createPrd: vi.fn(),
    assessPrd: vi.fn(),
    approvePrd: vi.fn(),
    waivePrd: vi.fn(),
    readyPrd: vi.fn(),
    createDelivery: vi.fn(),
    addPrdSource: vi.fn(),
    connectPrdSource: vi.fn(),
    uploadPrdSource: vi.fn(),
    createEvidence: vi.fn(),
    confirmEvidence: vi.fn(),
  },
  getStoredApiKey: vi.fn(() => ''),
  setStoredApiKey: vi.fn((value) => value),
}));

import { api } from '../workspaceApi';
import App from '../App';

function renderApp(path = '/workspace') {
  window.history.replaceState({}, '', path);
  return render(
    <BrowserRouter>
      <App />
    </BrowserRouter>,
  );
}

describe('workspace navigation', () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    api.listProjects.mockResolvedValue({ projects: [{ id: 'p-1', name: 'Mobile' }] });
    api.summary.mockResolvedValue({ counts: { evidence: 0 }, pending_approvals: 0, ready_for_delivery: 0 });
    api.listConversations.mockResolvedValue({ conversations: [] });
    api.getConversation.mockResolvedValue({ conversation: { id: 'c-1', title: '工作台对话' }, messages: [], tasks: [] });
    api.listPendingTasks.mockResolvedValue({ items: [] });
    api.getProject.mockResolvedValue({ id: 'p-1', sources: [] });
    api.listEvidence.mockResolvedValue({ evidence: [] });
    api.listInsights.mockResolvedValue({ insights: [] });
    api.listOpportunities.mockResolvedValue({ opportunities: [] });
    api.listPrds.mockResolvedValue({ prd_versions: [] });
    api.listDeliveries.mockResolvedValue({ deliveries: [] });
    api.listProviderConnections.mockResolvedValue({ connections: [], master_key_configured: false });
    api.createInsight.mockResolvedValue({ insight: { id: 'i-1' } });
    api.submitOpportunity.mockResolvedValue({});
    api.approveOpportunity.mockResolvedValue({});
    api.createDelivery.mockResolvedValue({ delivery: { id: 'd-1' } });
    api.connectPrdSource.mockResolvedValue({ id: 's-1', version: 1 });
    api.uploadPrdSource.mockResolvedValue({ id: 's-2', version: 1 });
    api.addPrdSource.mockResolvedValue({ id: 's-3', version: 1 });
    api.createConversation.mockResolvedValue({ conversation: { id: 'c-new' } });
    api.sendMessage.mockResolvedValue({ conversation: { id: 'c-new' }, task: { id: 't-new' } });
    api.getReviewStatus.mockResolvedValue({
      run_id: 'run-9',
      status: 'completed',
      progress: { percent: 100, current_node: 'finalize_artifacts', updated_at: '2026-08-11T09:00:00Z' },
    });
  });

  it('renders the six workbench routes', async () => {
    renderApp('/workspace');
    await waitFor(() => expect(screen.getByRole('heading', { name: '工作台' })).toBeInTheDocument());
    ['工作台', '资料', '决策', '成果', '待确认', '设置'].forEach((name) =>
      expect(screen.getByRole('link', { name })).toBeInTheDocument(),
    );
  });

  it('redirects legacy /run/:runId links into the deliveries workspace', async () => {
    api.lookupProjectByRun.mockResolvedValue({ project_id: 'p-1', run_id: 'run-9' });
    renderApp('/run/run-9?embed=feishu&open_id=ou_x');
    await waitFor(() => expect(api.lookupProjectByRun).toHaveBeenCalledWith('run-9'));
    await waitFor(() => {
      expect(window.location.pathname).toBe('/deliveries');
      expect(window.location.search).toContain('project_id=p-1');
      expect(window.location.search).toContain('run_id=run-9');
      expect(window.location.search).toContain('embed=feishu');
    });
  });

  it('auto-selects a remembered or ranked project on first paint', async () => {
    api.listProjects.mockResolvedValue({
      projects: [
        { id: 'weak', name: 'Test Project', updated_at: '2026-01-01' },
        { id: 'p-2', name: '续费漏斗', updated_at: '2026-08-01', source_count: 3 },
      ],
    });
    renderApp('/workspace');
    await waitFor(() => expect(screen.getByLabelText('选择项目')).toHaveValue('p-2'));
    expect(localStorage.getItem('pm-pal-last-project')).toBe('p-2');
  });

  it('filters the project picker by search text', async () => {
    const user = userEvent.setup();
    api.listProjects.mockResolvedValue({
      projects: [
        { id: 'p-a', name: 'Alpha 产品' },
        { id: 'p-b', name: 'Beta 增长' },
      ],
    });
    renderApp('/workspace?project_id=p-a');
    await waitFor(() => expect(screen.getByLabelText('选择项目')).toBeInTheDocument());
    await user.type(screen.getByLabelText('搜索项目'), 'Beta');
    expect(screen.getByText('共 2 个项目，匹配 1 个')).toBeInTheDocument();
    const picker = screen.getByLabelText('选择项目');
    expect(within(picker).getByText('Beta 增长')).toBeInTheDocument();
    // Keep the current selection visible so the select value stays valid.
    expect(within(picker).getByText('Alpha 产品')).toBeInTheDocument();
  });

  it('keeps settings reachable without a service API key form', async () => {
    api.listProjects.mockResolvedValue({ projects: [] });
    renderApp('/settings');
    await waitFor(() => expect(screen.getByRole('heading', { name: '设置' })).toBeInTheDocument());
    expect(screen.getByText(/本机桌面版默认免服务鉴权/)).toBeInTheDocument();
    expect(screen.getByText('模型连接状态')).toBeInTheDocument();
    expect(screen.queryByLabelText('服务 API Key')).not.toBeInTheDocument();
  });

  it('opens conversation detail from recent chat cards', async () => {
    const user = userEvent.setup();
    api.listConversations.mockResolvedValue({
      conversations: [{ id: 'c-1', title: '续费漏斗', updated_at: '2026-08-03', latest_task_status: 'awaiting_confirmation' }],
    });
    api.getConversation.mockResolvedValue({
      conversation: { id: 'c-1', title: '续费漏斗' },
      messages: [{ id: 'm-1', role: 'assistant', content: '已准备评审命令' }],
      tasks: [{ id: 't-1', title: '发起 PRD 评审', status: 'awaiting_confirmation', details: { next_step: '请确认后执行' } }],
    });
    renderApp('/workspace?project_id=p-1');
    await waitFor(() => expect(screen.getByText('续费漏斗')).toBeInTheDocument());
    await user.click(screen.getByText('续费漏斗'));
    await waitFor(() => expect(api.getConversation).toHaveBeenCalledWith('c-1'));
    await waitFor(() => expect(screen.getByText('已准备评审命令')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: '确认执行' })).toBeInTheDocument();
  });

  it('supports decision write actions for opportunities', async () => {
    const user = userEvent.setup();
    api.listOpportunities.mockResolvedValue({
      opportunities: [{ id: 'o-1', title: '缩短审批链路', problem: '审批过慢', status: 'proposed' }],
    });
    renderApp('/decisions?project_id=p-1');
    await waitFor(() => expect(screen.getByRole('heading', { name: '决策' })).toBeInTheDocument());
    await user.click(screen.getByRole('tab', { name: '机会' }));
    await waitFor(() => expect(screen.getByText('缩短审批链路')).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: '提交审批' }));
    await waitFor(() => expect(api.submitOpportunity).toHaveBeenCalledWith('p-1', 'o-1'));
  });

  it('exports ready PRDs from the deliveries page', async () => {
    const user = userEvent.setup();
    api.listPrds.mockResolvedValue({
      prd_versions: [{ id: 'prd-1', title: '审批 PRD', status: 'ready_for_delivery' }],
    });
    renderApp('/deliveries?project_id=p-1');
    await waitFor(() => expect(screen.getByRole('heading', { level: 1, name: '成果' })).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: '导出交付' }));
    await waitFor(() => expect(api.createDelivery).toHaveBeenCalledWith('p-1', { prd_version_id: 'prd-1' }));
  });

  it('connects a Feishu PRD URL from materials', async () => {
    const user = userEvent.setup();
    renderApp('/materials?project_id=p-1');
    await waitFor(() => expect(screen.getByRole('heading', { name: '资料' })).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: '接入 PRD' }));
    const dialog = await screen.findByRole('dialog');
    const urlInput = within(dialog).getByLabelText('飞书文档链接');
    await user.click(urlInput);
    await user.paste('https://feishu.cn/docx/abc');
    fireEvent.submit(dialog.querySelector('form'));
    await waitFor(() => expect(api.connectPrdSource).toHaveBeenCalledWith('p-1', {
      source_url: 'https://feishu.cn/docx/abc',
      title: '',
    }));
  });

  it('uploads a local PRD file from materials', async () => {
    const user = userEvent.setup();
    const file = new File(['# Spec'], 'spec.md', { type: 'text/markdown' });
    renderApp('/materials?project_id=p-1');
    await waitFor(() => expect(screen.getByRole('heading', { name: '资料' })).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: '接入 PRD' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('tab', { name: '本地文件' }));
    const fileInput = within(screen.getByRole('dialog')).getByLabelText('选择文件');
    await user.upload(fileInput, file);
    expect(fileInput.files?.[0]).toBe(file);
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveTextContent('spec.md'));
    fireEvent.submit(screen.getByRole('dialog').querySelector('form'));
    await waitFor(() => expect(api.uploadPrdSource).toHaveBeenCalledWith('p-1', expect.any(File), { title: '' }));
  });

  it('still allows paste fallback for PRD text', async () => {
    const user = userEvent.setup();
    renderApp('/materials?project_id=p-1');
    await waitFor(() => expect(screen.getByRole('heading', { name: '资料' })).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: '接入 PRD' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('tab', { name: '粘贴正文' }));
    const openDialog = () => screen.getByRole('dialog');
    const titleInput = within(openDialog()).getByLabelText('PRD 标题');
    const contentInput = within(openDialog()).getByLabelText('PRD 正文');
    await user.click(titleInput);
    await user.paste('临时稿');
    await user.click(contentInput);
    await user.paste('粘贴内容');
    fireEvent.submit(openDialog().querySelector('form'));
    await waitFor(() => expect(api.addPrdSource).toHaveBeenCalledWith('p-1', {
      title: '临时稿',
      content: '粘贴内容',
      source_type: 'prd_text',
    }));
  });

  it('submits ask-agent with an explicit action', async () => {
    const user = userEvent.setup();
    renderApp('/workspace?project_id=p-1');
    await waitFor(() => expect(screen.getByRole('heading', { name: '工作台' })).toBeInTheDocument());
    await user.click(screen.getAllByRole('button', { name: '询问 Agent' })[0]);
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: '生成机会草案' }));
    const taskInput = within(dialog).getByLabelText('任务说明');
    await user.click(taskInput);
    await user.paste('请生成机会草案');
    fireEvent.submit(screen.getByRole('dialog').querySelector('form'));
    await waitFor(() => expect(api.createConversation).toHaveBeenCalledWith('p-1', 'local'));
    await waitFor(() => expect(api.sendMessage).toHaveBeenCalledWith(
      'c-new',
      '请生成机会草案',
      'local',
      { action: 'generate_opportunity' },
    ));
  });

  it('shows review progress on deliveries when run_id is present', async () => {
    renderApp('/deliveries?project_id=p-1&run_id=run-9');
    await waitFor(() => expect(screen.getByRole('heading', { level: 1, name: '成果' })).toBeInTheDocument());
    await waitFor(() => expect(api.getReviewStatus).toHaveBeenCalledWith('p-1', 'run-9'));
    expect(screen.getByRole('heading', { level: 2, name: '评审进度' })).toBeInTheDocument();
    expect(screen.getByText(/100%/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '打开 Markdown 报告' })).toBeInTheDocument();
  });

  it('starts a review from a materials PRD card', async () => {
    const user = userEvent.setup();
    api.getProject.mockResolvedValue({
      id: 'p-1',
      sources: [{ id: 'src-1', title: '用户管理 PRD', source_type: 'prd_text', is_prd: true, version: 1 }],
    });
    renderApp('/materials?project_id=p-1');
    await waitFor(() => expect(screen.getByText('用户管理 PRD')).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: '发起评审' }));
    await waitFor(() => expect(api.sendMessage).toHaveBeenCalledWith(
      'c-new',
      expect.stringContaining('用户管理 PRD'),
      'local',
      { action: 'start_review', source_id: 'src-1' },
    ));
  });
});
