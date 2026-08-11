import { useCallback, useEffect, useState } from 'react';
import {
  Badge, Button, Card, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle,
  Field, FluentProvider, Input, Spinner, Tab, TabList, Text, Textarea, Toaster, Toast,
  useId, useToastController, webDarkTheme, webLightTheme,
} from '@fluentui/react-components';
import {
  IconBook2, IconBox, IconChecklist, IconLayoutDashboard, IconMoon, IconPlus, IconRefresh,
  IconRobot, IconSettings, IconSparkles, IconSun,
} from '@tabler/icons-react';
import { NavLink, Navigate, Route, Routes, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api, ApiError } from './workspaceApi';
import WorkspaceReviewProgress from './components/WorkspaceReviewProgress';
import { formatSourceType, formatStatus } from './utils/presentation';
import { formatDateTime } from './utils/formatters';
import './styles/workspace-v5.css';

const T = {
  workspace: '工作台',
  materials: '资料',
  decisions: '决策',
  deliveries: '成果',
  confirmations: '待确认',
  pendingApprovals: '待审批机会',
  settings: '设置',
  navigation: '主导航',
  theme: '切换主题',
  refresh: '刷新',
  ask: '询问 Agent',
  agentAction: '动作类型',
  agentActionHint: '先选择要执行的动作，再补充说明。',
  startReview: '发起评审',
  viewReviewProgress: '查看评审进度',
  reviewProgress: '评审进度',
  ambiguousIntent: '这句话匹配多个动作，请选择一项：',
  create: '新建项目',
  current: '当前项目',
  choose: '选择项目',
  retry: '重试',
  cancel: '取消',
  submit: '提交任务',
  createAction: '创建',
  saving: '正在提交',
  projectName: '项目名称',
  taskHint: '任务会关联到当前项目空间。',
  taskLabel: '任务说明',
  taskPlaceholder: '例如：分析已确认的反馈，生成待审批机会',
  collectedMaterials: '已收集资料',
  readyPrd: '可交付 PRD',
  startFromProject: '从当前项目开始',
  startHint: '向 Agent 提出任务，结果会进入资料、决策或待确认队列。',
  recentChats: '最近对话',
  noTasks: '还没有任务。用「询问 Agent」开始第一项工作。',
  loadingWorkspace: '正在加载工作台',
  loadingMaterials: '正在加载资料',
  loadingDecisions: '正在加载决策',
  loadingDeliveries: '正在加载成果',
  loadingConfirmations: '正在加载待确认事项',
  loadingConversation: '正在加载对话',
  filterEvidence: '筛选证据',
  keywordPlaceholder: '输入关键词',
  prdSources: 'PRD 来源',
  evidence: '证据',
  noPrdSources: '当前项目还没有 PRD 来源。',
  noEvidence: '没有匹配的证据。',
  unknownSource: '未知来源',
  confirmed: '已确认',
  confirmEvidence: '确认证据',
  addEvidence: '添加证据',
  addPrd: '接入 PRD',
  evidenceContent: '证据内容',
  confirmOnSave: '保存后立即确认',
  prdTitle: 'PRD 标题',
  prdContent: 'PRD 正文',
  prdModeFeishu: '飞书链接',
  prdModeFile: '本地文件',
  prdModePaste: '粘贴正文',
  prdFeishuUrl: '飞书文档链接',
  prdFeishuHint: '粘贴飞书/Lark 文档 URL，将拉取正文快照到本机项目。需配置 MARRDP_FEISHU_APP_ID / APP_SECRET。',
  prdFileHint: '支持 .md / .txt / .pdf / .docx，上传后写入项目快照。',
  prdPasteHint: '仅作兜底：临时草稿或无法连接外部文档时使用。',
  prdOptionalTitle: '标题（可选）',
  chooseFile: '选择文件',
  noFileChosen: '尚未选择文件',
  openSource: '打开来源',
  feishuSetupHint: '飞书文档接入需在 .env 配置 MARRDP_FEISHU_APP_ID / MARRDP_FEISHU_APP_SECRET（或项目级 connectors/feishu）。',
  insights: '洞察',
  opportunities: '机会',
  waitingContent: '等待补充内容',
  noItems: (title) => `暂时没有${title}。`,
  deliveryRecord: '交付记录',
  noDeliveries: '当前项目没有交付记录。',
  nextStepFallback: 'Agent 建议的变更',
  confirmRun: '确认执行',
  dismissRun: '暂不执行',
  noConfirmations: '没有需要确认的 Agent 操作。',
  needProject: '请先新建一个项目以开始工作。',
  needProjectPage: '请先选择或新建一个项目。',
  goSettings: '打开设置',
  taskCreated: '任务已创建。',
  confirmOk: '已确认执行。',
  dismissOk: '已忽略该任务。',
  confirmFail: '操作失败',
  providerStatus: '模型连接状态',
  masterKeyOk: '密钥主密钥已配置，可在后续版本管理 provider',
  masterKeyMissing: '未配置 PM_PAL_SECRETS_MASTER_KEY；当前可用 .env 中的模型 Key 运行',
  connectionCount: (n) => `${n} 个已保存连接`,
  desktopHint: '本机桌面版默认免服务鉴权，后端仅监听本机回环地址。',
  backToList: '返回对话列表',
  messages: '消息',
  tasks: '任务',
  noMessages: '暂无消息。',
  evidenceSaved: '证据已保存。',
  evidenceConfirmed: '证据已确认。',
  prdSaved: 'PRD 来源已添加。',
  reviewQueued: '评审任务已创建，请确认后执行。',
  loadingProjects: '正在加载项目',
  searchProjects: '搜索项目',
  projectCount: (n, shown, searching = false) => (
    searching
      ? `共 ${n} 个项目，匹配 ${shown} 个`
      : `共 ${n} 个项目，当前列表 ${shown} 个`
  ),
  addInsight: '添加洞察',
  addOpportunity: '添加机会',
  insightTitle: '洞察标题',
  insightSummary: '摘要',
  opportunityTitle: '机会标题',
  opportunityProblem: '问题描述',
  submitApproval: '提交审批',
  approve: '批准',
  reject: '驳回',
  createPrdFromOpp: '生成 PRD',
  assessQuality: '质量评估',
  markReady: '标记可交付',
  waiveQuality: '豁免质量门',
  exportDelivery: '导出交付',
  noReadyPrd: '没有可导出的 PRD（需先标记为可交付）。',
  actionOk: '操作成功。',
  insightSaved: '洞察已创建。',
  opportunitySaved: '机会已创建。',
  prdVersionSaved: 'PRD 版本已创建。',
  deliverySaved: '交付已创建。',
};

const AGENT_ACTIONS = [
  { value: 'start_review', label: '发起 PRD 评审' },
  { value: 'generate_opportunity', label: '生成机会草案' },
  { value: 'generate_prd', label: '生成正式 PRD' },
  { value: 'prepare_delivery', label: '准备交付包' },
];

const LAST_PROJECT_KEY = 'pm-pal-last-project';
const PROJECT_PICKER_LIMIT = 40;

/** Legacy Feishu/card links land on /run/:runId; resolve project and open workspace shell. :-) */
function RunRedirect() {
  const { runId } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    const targetRunId = String(runId || '').trim();
    if (!targetRunId) {
      navigate('/workspace', { replace: true });
      return undefined;
    }

    (async () => {
      try {
        const data = await api.lookupProjectByRun(targetRunId);
        if (cancelled) return;
        const params = new URLSearchParams(searchParams);
        params.set('project_id', data.project_id);
        params.set('run_id', targetRunId);
        navigate(`/deliveries?${params.toString()}`, { replace: true });
      } catch (err) {
        if (cancelled) return;
        setError(err?.message || '无法定位评审所属项目');
        navigate('/workspace', { replace: true });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [runId, searchParams, navigate]);

  if (error) {
    return <Text>{error}</Text>;
  }
  return (
    <div className="v5-empty">
      <Spinner size="medium" />
      <Text>{T.loadingWorkspace}</Text>
    </div>
  );
}

function LegacyProjectReviewRedirect() {
  const { projectId, runId } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  useEffect(() => {
    const params = new URLSearchParams(searchParams);
    if (projectId) params.set('project_id', projectId);
    if (runId) params.set('run_id', runId);
    navigate(`/deliveries?${params.toString()}`, { replace: true });
  }, [projectId, runId, searchParams, navigate]);

  return null;
}

function isWeakProjectName(name) {
  const text = String(name || '').trim();
  if (!text) return true;
  if (text === 'Test Project') return true;
  if (/^[?\uFFFD\s.]+$/.test(text)) return true;
  if ((text.match(/\?/g) || []).length >= Math.max(2, text.length / 2)) return true;
  return false;
}

function projectLabel(item) {
  if (!item) return '';
  if (!isWeakProjectName(item.name)) return item.name;
  const short = String(item.id || '').replace(/^project_/, '').slice(0, 10);
  return short ? `未命名 · ${short}` : item.id;
}

function rankProjects(items) {
  return [...items].sort((a, b) => {
    const score = (item) => {
      let value = 0;
      if (isWeakProjectName(item.name)) value -= 50;
      else value += 20;
      value += Math.min(10, Number(item.source_count || 0) * 2);
      value += Math.min(8, Number(item.run_count || 0) * 2);
      return value;
    };
    const delta = score(b) - score(a);
    if (delta) return delta;
    return String(b.updated_at || '').localeCompare(String(a.updated_at || ''));
  });
}

function filterProjects(items, queryText) {
  const ranked = rankProjects(items);
  const needle = String(queryText || '').trim().toLowerCase();
  if (!needle) return ranked.slice(0, PROJECT_PICKER_LIMIT);
  return ranked
    .filter((item) => {
      const hay = `${item.name || ''} ${item.id || ''}`.toLowerCase();
      return hay.includes(needle);
    })
    .slice(0, PROJECT_PICKER_LIMIT);
}

const NAV = [
  ['/workspace', T.workspace, IconLayoutDashboard],
  ['/materials', T.materials, IconBook2],
  ['/decisions', T.decisions, IconSparkles],
  ['/deliveries', T.deliveries, IconBox],
  ['/confirmations', T.confirmations, IconChecklist],
  ['/settings', T.settings, IconSettings],
];

const color = (status) => (
  status === 'completed' || status === 'succeeded' ? 'success'
    : status === 'failed' || status === 'denied' ? 'danger'
      : status === 'running' || status === 'awaiting_confirmation' || status === 'pending_approval' ? 'warning'
        : 'informative'
);

const label = (status) => formatStatus(status);

function useData(projectId, load) {
  const [state, setState] = useState({ loading: true, error: '', data: null });
  const refresh = useCallback(async () => {
    if (!projectId) {
      setState({ loading: false, error: '', data: null });
      return;
    }
    setState((v) => ({ ...v, loading: true, error: '' }));
    try {
      setState({ loading: false, error: '', data: await load(projectId) });
    } catch (error) {
      setState((v) => ({ ...v, loading: false, error: error.message }));
    }
  }, [projectId, load]);
  useEffect(() => { refresh(); }, [refresh]);
  return { ...state, refresh };
}

function ErrorState({ error, retry, action }) {
  return (
    <Card className="v5-error" role="alert">
      <Text>{error}</Text>
      <div className="v5-actions">
        {action}
        {retry ? <Button appearance="secondary" onClick={retry}>{T.retry}</Button> : null}
      </div>
    </Card>
  );
}

function Empty({ children }) {
  return <Card className="v5-empty"><Text>{children}</Text></Card>;
}

function Header({ title, action }) {
  return <div className="v5-section-header"><h1>{title}</h1>{action}</div>;
}

function Loading({ label: text }) {
  return <Spinner label={text} />;
}

function NeedProject({ loading = false }) {
  if (loading) return <Loading label={T.loadingProjects} />;
  return <Empty>{T.needProjectPage}</Empty>;
}

function AgentDialog({ open, onOpenChange, projectId, onCreated }) {
  const [value, setValue] = useState('');
  const [action, setAction] = useState('start_review');
  const [candidates, setCandidates] = useState([]);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) {
      setValue('');
      setAction('start_review');
      setCandidates([]);
      setError('');
      setSaving(false);
    }
  }, [open]);

  async function save(event, forcedAction = action) {
    event.preventDefault();
    if (!value.trim() || !forcedAction) return;
    setSaving(true);
    setError('');
    try {
      const conversation = await api.createConversation(projectId, 'local');
      const result = await api.sendMessage(conversation.conversation.id, value.trim(), 'local', {
        action: forcedAction,
      });
      setValue('');
      setCandidates([]);
      onCreated(result);
      onOpenChange(false);
    } catch (err) {
      if (err instanceof ApiError && err.code === 'intent_ambiguous') {
        setCandidates(err.candidates || []);
        setError(err.message || T.ambiguousIntent);
      } else {
        setError(err.message);
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(_, data) => {
        // Ignore backdrop dismiss while the form is submitting or mid-input. :-)
        if (!data.open && saving) return;
        onOpenChange(data.open);
      }}
    >
      <DialogSurface aria-describedby={undefined}>
        <form onSubmit={(event) => save(event)}>
          <DialogBody>
            <DialogTitle>{T.ask}</DialogTitle>
            <DialogContent className="v5-dialog">
              <Text>{T.agentActionHint}</Text>
              <Field label={T.agentAction} required>
                <div className="v5-actions" role="radiogroup" aria-label={T.agentAction}>
                  {AGENT_ACTIONS.map((item) => (
                    <Button
                      key={item.value}
                      type="button"
                      size="small"
                      appearance={action === item.value ? 'primary' : 'secondary'}
                      aria-pressed={action === item.value}
                      onClick={() => setAction(item.value)}
                    >
                      {item.label}
                    </Button>
                  ))}
                </div>
              </Field>
              <Field label={T.taskLabel} required>
                <Textarea
                  aria-label={T.taskLabel}
                  value={value}
                  onChange={(_, data) => setValue(data.value)}
                  placeholder={T.taskPlaceholder}
                  resize="vertical"
                />
              </Field>
              {candidates.length ? (
                <div className="v5-actions">
                  <Text>{T.ambiguousIntent}</Text>
                  {candidates.map((item) => (
                    <Button
                      key={item.action || item}
                      type="button"
                      size="small"
                      onClick={(event) => {
                        const next = item.action || item;
                        setAction(next);
                        save(event, next);
                      }}
                    >
                      {item.title || item.action || item}
                    </Button>
                  ))}
                </div>
              ) : null}
              {error ? <Text role="alert" className="v5-inline-error">{error}</Text> : null}
            </DialogContent>
            <DialogActions>
              <Button type="button" appearance="secondary" onClick={() => onOpenChange(false)}>{T.cancel}</Button>
              <Button appearance="primary" type="submit" disabled={!value.trim() || !action || saving}>
                {saving ? T.saving : T.submit}
              </Button>
            </DialogActions>
          </DialogBody>
        </form>
      </DialogSurface>
    </Dialog>
  );
}

function ProjectDialog({ open, onOpenChange, onCreated }) {
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  async function create(event) {
    event.preventDefault();
    try {
      const result = await api.createProject(name.trim());
      setName('');
      onCreated(result);
      onOpenChange(false);
    } catch (err) {
      setError(err.message);
    }
  }
  return (
    <Dialog open={open} onOpenChange={(_, data) => onOpenChange(data.open)}>
      <DialogSurface>
        <form onSubmit={create}>
          <DialogBody>
            <DialogTitle>{T.create}</DialogTitle>
            <DialogContent>
              <Field label={T.projectName} required>
                <Input value={name} onChange={(_, data) => setName(data.value)} autoFocus />
              </Field>
              {error ? <Text role="alert" className="v5-inline-error">{error}</Text> : null}
            </DialogContent>
            <DialogActions>
              <Button appearance="secondary" onClick={() => onOpenChange(false)}>{T.cancel}</Button>
              <Button appearance="primary" type="submit" disabled={!name.trim()}>{T.createAction}</Button>
            </DialogActions>
          </DialogBody>
        </form>
      </DialogSurface>
    </Dialog>
  );
}

function TaskActions({ taskId, onResolved, toast }) {
  const [busy, setBusy] = useState(false);
  async function resolve(action) {
    setBusy(true);
    try {
      if (action === 'confirm') await api.confirmTask(taskId);
      else await api.dismissTask(taskId);
      toast(action === 'confirm' ? T.confirmOk : T.dismissOk, 'success');
      onResolved?.();
    } catch (error) {
      toast(error.message || T.confirmFail, 'error');
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="v5-actions">
      <Button appearance="primary" disabled={busy} onClick={() => resolve('confirm')}>{T.confirmRun}</Button>
      <Button appearance="secondary" disabled={busy} onClick={() => resolve('dismiss')}>{T.dismissRun}</Button>
    </div>
  );
}

function ConversationDetail({ conversationId, projectId, onBack, toast }) {
  const navigate = useNavigate();
  const load = useCallback(async () => api.getConversation(conversationId), [conversationId]);
  const [state, setState] = useState({ loading: true, error: '', data: null });
  const refresh = useCallback(async () => {
    setState((v) => ({ ...v, loading: true, error: '' }));
    try {
      setState({ loading: false, error: '', data: await load() });
    } catch (error) {
      setState((v) => ({ ...v, loading: false, error: error.message }));
    }
  }, [load]);
  useEffect(() => { refresh(); }, [refresh]);
  if (state.loading && !state.data) return <Loading label={T.loadingConversation} />;
  if (state.error) return <ErrorState error={state.error} retry={refresh} />;
  const detail = state.data;
  const title = detail.conversation?.title || conversationId;
  return (
    <>
      <Header
        title={title}
        action={<Button appearance="secondary" onClick={onBack}>{T.backToList}</Button>}
      />
      <h2 className="v5-subtitle">{T.messages}</h2>
      <div className="v5-list">
        {(detail.messages || []).length
          ? detail.messages.map((item) => (
            <Card key={item.id} className="v5-item">
              <Text size={200}>{item.role}</Text>
              <strong>{item.content}</strong>
            </Card>
          ))
          : <Empty>{T.noMessages}</Empty>}
      </div>
      <h2 className="v5-subtitle">{T.tasks}</h2>
      <div className="v5-list">
        {(detail.tasks || []).length
          ? detail.tasks.map((item) => {
            const runId = item.details?.result?.run_id || '';
            return (
              <Card key={item.id} className="v5-row">
                <div>
                  <strong>{item.title || item.kind}</strong>
                  <Text size={200}>{item.details?.next_step || T.nextStepFallback}</Text>
                </div>
                <div className="v5-actions">
                  <Badge color={color(item.status)}>{label(item.status)}</Badge>
                  {item.status === 'awaiting_confirmation'
                    ? <TaskActions taskId={item.id} onResolved={refresh} toast={toast} />
                    : null}
                  {runId && projectId ? (
                    <Button
                      appearance="primary"
                      size="small"
                      onClick={() => navigate(`/deliveries?project_id=${encodeURIComponent(projectId)}&run_id=${encodeURIComponent(runId)}`)}
                    >
                      {T.viewReviewProgress}
                    </Button>
                  ) : null}
                </div>
              </Card>
            );
          })
          : <Empty>{T.noConfirmations}</Empty>}
      </div>
    </>
  );
}

function Workspace({ projectId, awaitingProject, openAgent, conversationId, onOpenConversation, onCloseConversation, toast }) {
  if (awaitingProject) return <NeedProject loading />;
  if (!projectId) return <NeedProject />;
  if (conversationId) {
    return <ConversationDetail conversationId={conversationId} projectId={projectId} onBack={onCloseConversation} toast={toast} />;
  }
  const load = useCallback(async (id) => {
    const [summary, conversations] = await Promise.all([
      api.summary(id),
      api.listConversations(id),
    ]);
    return { summary, conversations: conversations.conversations || [] };
  }, []);
  const { loading, error, data, refresh } = useData(projectId, load);
  if (!data && !error) return <Loading label={T.loadingWorkspace} />;
  if (error) return <ErrorState error={error} retry={refresh} />;
  const summary = data.summary || { counts: {} };
  const pendingFromConversations = data.conversations.filter((item) => item.latest_task_status === 'awaiting_confirmation').length;
  return (
    <>
      <Header title={T.workspace} action={<Button appearance="secondary" icon={<IconRefresh size={16} />} onClick={refresh}>{T.refresh}</Button>} />
      <div className="v5-summary">
        <Card><Text>{T.confirmations}</Text><strong>{pendingFromConversations}</strong></Card>
        <Card><Text>{T.pendingApprovals}</Text><strong>{summary.pending_approvals || 0}</strong></Card>
        <Card><Text>{T.collectedMaterials}</Text><strong>{summary.counts?.evidence || 0}</strong></Card>
        <Card><Text>{T.readyPrd}</Text><strong>{summary.ready_for_delivery || 0}</strong></Card>
      </div>
      <Card className="v5-callout">
        <div>
          <h2>{T.startFromProject}</h2>
          <Text>{T.startHint}</Text>
        </div>
        <Button appearance="primary" icon={<IconRobot size={17} />} onClick={openAgent}>{T.ask}</Button>
      </Card>
      <h2 className="v5-subtitle">{T.recentChats}</h2>
      <div className="v5-list">
        {data.conversations.length
          ? data.conversations.map((item) => (
            <Card
              key={item.id}
              className="v5-row v5-row-clickable"
              role="button"
              tabIndex={0}
              onClick={() => onOpenConversation(item.id)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  onOpenConversation(item.id);
                }
              }}
            >
              <div>
                <strong>{item.title}</strong>
                <Text size={200}>{formatDateTime(item.updated_at)}</Text>
              </div>
              <Badge color={color(item.latest_task_status)}>{label(item.latest_task_status)}</Badge>
            </Card>
          ))
          : <Empty>{T.noTasks}</Empty>}
      </div>
    </>
  );
}

function EvidenceDialog({ open, onOpenChange, projectId, onSaved }) {
  const [content, setContent] = useState('');
  const [confirm, setConfirm] = useState(true);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  async function save(event) {
    event.preventDefault();
    if (!content.trim()) return;
    setSaving(true);
    setError('');
    try {
      await api.createEvidence(projectId, { content: content.trim(), confirm, display_name: '手动反馈' });
      setContent('');
      onSaved();
      onOpenChange(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <Dialog open={open} onOpenChange={(_, data) => onOpenChange(data.open)}>
      <DialogSurface>
        <form onSubmit={save}>
          <DialogBody>
            <DialogTitle>{T.addEvidence}</DialogTitle>
            <DialogContent className="v5-dialog">
              <Field label={T.evidenceContent} required>
                <Textarea value={content} onChange={(_, data) => setContent(data.value)} resize="vertical" />
              </Field>
              <label className="v5-check">
                <input type="checkbox" checked={confirm} onChange={(event) => setConfirm(event.target.checked)} />
                {T.confirmOnSave}
              </label>
              {error ? <Text role="alert" className="v5-inline-error">{error}</Text> : null}
            </DialogContent>
            <DialogActions>
              <Button appearance="secondary" onClick={() => onOpenChange(false)}>{T.cancel}</Button>
              <Button appearance="primary" type="submit" disabled={!content.trim() || saving}>{saving ? T.saving : T.addEvidence}</Button>
            </DialogActions>
          </DialogBody>
        </form>
      </DialogSurface>
    </Dialog>
  );
}

function PrdDialog({ open, onOpenChange, projectId, onSaved }) {
  const [mode, setMode] = useState('feishu');
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [file, setFile] = useState(null);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  function reset() {
    setTitle('');
    setContent('');
    setSourceUrl('');
    setFile(null);
    setError('');
  }

  async function save(event) {
    event.preventDefault();
    if (mode === 'feishu' && !sourceUrl.trim()) return;
    if (mode === 'file' && !file) return;
    if (mode === 'paste' && (!title.trim() || !content.trim())) return;
    setSaving(true);
    setError('');
    try {
      if (mode === 'feishu') {
        await api.connectPrdSource(projectId, { source_url: sourceUrl.trim(), title: title.trim() });
      } else if (mode === 'file') {
        await api.uploadPrdSource(projectId, file, { title: title.trim() });
      } else {
        await api.addPrdSource(projectId, { title: title.trim(), content: content.trim(), source_type: 'prd_text' });
      }
      reset();
      onSaved();
      onOpenChange(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  const canSubmit = mode === 'feishu'
    ? Boolean(sourceUrl.trim())
    : mode === 'file'
      ? Boolean(file)
      : Boolean(title.trim() && content.trim());

  return (
    <Dialog open={open} onOpenChange={(_, data) => { if (!data.open) reset(); onOpenChange(data.open); }}>
      <DialogSurface>
        <form onSubmit={save}>
          <DialogBody>
            <DialogTitle>{T.addPrd}</DialogTitle>
            <DialogContent className="v5-dialog">
              <TabList selectedValue={mode} onTabSelect={(_, data) => { setMode(data.value); setError(''); }}>
                <Tab value="feishu">{T.prdModeFeishu}</Tab>
                <Tab value="file">{T.prdModeFile}</Tab>
                <Tab value="paste">{T.prdModePaste}</Tab>
              </TabList>
              {mode === 'feishu' ? (
                <>
                  <Text size={200}>{T.prdFeishuHint}</Text>
                  <Field label={T.prdFeishuUrl} required>
                    <Input
                      aria-label={T.prdFeishuUrl}
                      value={sourceUrl}
                      onChange={(_, data) => setSourceUrl(data.value)}
                      placeholder="https://..."
                    />
                  </Field>
                  <Field label={T.prdOptionalTitle}>
                    <Input aria-label={T.prdOptionalTitle} value={title} onChange={(_, data) => setTitle(data.value)} />
                  </Field>
                </>
              ) : null}
              {mode === 'file' ? (
                <>
                  <Text size={200}>{T.prdFileHint}</Text>
                  <Field label={T.chooseFile} required>
                    <input
                      type="file"
                      aria-label={T.chooseFile}
                      accept=".md,.txt,.pdf,.docx,text/markdown,text/plain"
                      onChange={(event) => setFile(event.target.files?.[0] || null)}
                    />
                  </Field>
                  <Text size={200}>{file ? file.name : T.noFileChosen}</Text>
                  <Field label={T.prdOptionalTitle}>
                    <Input aria-label={T.prdOptionalTitle} value={title} onChange={(_, data) => setTitle(data.value)} />
                  </Field>
                </>
              ) : null}
              {mode === 'paste' ? (
                <>
                  <Text size={200}>{T.prdPasteHint}</Text>
                  <Field label={T.prdTitle} required>
                    <Input aria-label={T.prdTitle} value={title} onChange={(_, data) => setTitle(data.value)} />
                  </Field>
                  <Field label={T.prdContent} required>
                    <Textarea aria-label={T.prdContent} value={content} onChange={(_, data) => setContent(data.value)} resize="vertical" />
                  </Field>
                </>
              ) : null}
              {error ? <Text role="alert" className="v5-inline-error">{error}</Text> : null}
            </DialogContent>
            <DialogActions>
              <Button appearance="secondary" onClick={() => onOpenChange(false)}>{T.cancel}</Button>
              <Button appearance="primary" type="submit" disabled={!canSubmit || saving}>{saving ? T.saving : T.addPrd}</Button>
            </DialogActions>
          </DialogBody>
        </form>
      </DialogSurface>
    </Dialog>
  );
}

function Materials({ projectId, awaitingProject, toast, onReviewStarted }) {
  const [queryText, setQueryText] = useState('');
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [prdOpen, setPrdOpen] = useState(false);
  const [reviewBusyId, setReviewBusyId] = useState('');
  const load = useCallback(async (id) => {
    const [project, evidence] = await Promise.all([
      api.getProject(id),
      api.listEvidence(id, queryText),
    ]);
    return {
      prdSources: (project.sources || []).filter((item) => item.is_prd),
      evidence: evidence.evidence || [],
    };
  }, [queryText]);
  const { error, data, refresh } = useData(projectId, load);
  async function confirmEvidence(evidenceId) {
    try {
      await api.confirmEvidence(projectId, evidenceId, true);
      toast(T.evidenceConfirmed, 'success');
      refresh();
    } catch (err) {
      toast(err.message || T.confirmFail, 'error');
    }
  }
  async function startReview(source) {
    setReviewBusyId(source.id);
    try {
      const conversation = await api.createConversation(projectId, 'local');
      const result = await api.sendMessage(
        conversation.conversation.id,
        `请对 PRD「${source.title || source.id}」发起需求完整性评审。`,
        'local',
        { action: 'start_review', source_id: source.id },
      );
      toast(T.reviewQueued, 'success');
      onReviewStarted?.(result);
    } catch (err) {
      toast(err.message || T.confirmFail, 'error');
    } finally {
      setReviewBusyId('');
    }
  }
  if (awaitingProject) return <NeedProject loading />;
  if (!projectId) return <NeedProject />;
  if (!data && !error) return <Loading label={T.loadingMaterials} />;
  if (error) return <ErrorState error={error} retry={refresh} />;
  return (
    <>
      <Header
        title={T.materials}
        action={(
          <div className="v5-actions">
            <Button appearance="secondary" onClick={refresh}>{T.refresh}</Button>
            <Button appearance="secondary" onClick={() => setEvidenceOpen(true)}>{T.addEvidence}</Button>
            <Button appearance="primary" onClick={() => setPrdOpen(true)}>{T.addPrd}</Button>
          </div>
        )}
      />
      <Field label={T.filterEvidence}>
        <Input value={queryText} onChange={(_, data) => setQueryText(data.value)} placeholder={T.keywordPlaceholder} />
      </Field>
      <div className="v5-two-column">
        <section>
          <h2>{T.prdSources}</h2>
          {data.prdSources.length
            ? data.prdSources.map((item) => (
              <Card className="v5-item" key={item.id}>
                <strong>{item.title || item.id}</strong>
                <Text>{formatSourceType(item.source_type)}{item.version ? ` · v${item.version}` : ''}</Text>
                <div className="v5-actions">
                  {item.source_url ? (
                    <a href={item.source_url} target="_blank" rel="noreferrer">{T.openSource}</a>
                  ) : null}
                  <Button
                    size="small"
                    appearance="primary"
                    disabled={reviewBusyId === item.id}
                    onClick={() => startReview(item)}
                  >
                    {reviewBusyId === item.id ? T.saving : T.startReview}
                  </Button>
                </div>
              </Card>
            ))
            : <Empty>{T.noPrdSources}</Empty>}
        </section>
        <section>
          <h2>{T.evidence}</h2>
          {data.evidence.length
            ? data.evidence.map((item) => (
              <Card className="v5-item" key={item.id}>
                <strong>{item.summary || item.content?.slice(0, 80)}</strong>
                <Text size={200}>
                  {item.author || T.unknownSource}
                  {item.confirmed ? ` · ${T.confirmed}` : ''}
                </Text>
                {!item.confirmed
                  ? <Button size="small" appearance="secondary" onClick={() => confirmEvidence(item.id)}>{T.confirmEvidence}</Button>
                  : null}
              </Card>
            ))
            : <Empty>{T.noEvidence}</Empty>}
        </section>
      </div>
      <EvidenceDialog
        open={evidenceOpen}
        onOpenChange={setEvidenceOpen}
        projectId={projectId}
        onSaved={() => { toast(T.evidenceSaved, 'success'); refresh(); }}
      />
      <PrdDialog
        open={prdOpen}
        onOpenChange={setPrdOpen}
        projectId={projectId}
        onSaved={() => { toast(T.prdSaved, 'success'); refresh(); }}
      />
    </>
  );
}

function Decisions({ projectId, awaitingProject, toast }) {
  const [tab, setTab] = useState('insights');
  const [insightOpen, setInsightOpen] = useState(false);
  const [opportunityOpen, setOpportunityOpen] = useState(false);
  const [busyId, setBusyId] = useState('');
  const load = useCallback(async (id) => {
    const [insights, opportunities, prds] = await Promise.all([
      api.listInsights(id),
      api.listOpportunities(id),
      api.listPrds(id),
    ]);
    return {
      insights: insights.insights || [],
      opportunities: opportunities.opportunities || [],
      prds: prds.prd_versions || [],
    };
  }, []);
  const { error, data, refresh } = useData(projectId, load);

  async function runAction(key, action) {
    setBusyId(key);
    try {
      await action();
      toast(T.actionOk, 'success');
      refresh();
    } catch (err) {
      toast(err.message || T.confirmFail, 'error');
    } finally {
      setBusyId('');
    }
  }

  if (awaitingProject) return <NeedProject loading />;
  if (!projectId) return <NeedProject />;
  if (!data && !error) return <Loading label={T.loadingDecisions} />;
  if (error) return <ErrorState error={error} retry={refresh} />;
  const title = { insights: T.insights, opportunities: T.opportunities, prds: 'PRD' }[tab];
  const rows = tab === 'prds' ? data.prds : data[tab];

  return (
    <>
      <Header
        title={T.decisions}
        action={(
          <div className="v5-actions">
            <Button appearance="secondary" onClick={refresh}>{T.refresh}</Button>
            {tab === 'insights' ? <Button appearance="primary" onClick={() => setInsightOpen(true)}>{T.addInsight}</Button> : null}
            {tab === 'opportunities' ? <Button appearance="primary" onClick={() => setOpportunityOpen(true)}>{T.addOpportunity}</Button> : null}
          </div>
        )}
      />
      <TabList selectedValue={tab} onTabSelect={(_, data) => setTab(data.value)}>
        <Tab value="insights">{T.insights}</Tab>
        <Tab value="opportunities">{T.opportunities}</Tab>
        <Tab value="prds">PRD</Tab>
      </TabList>
      <div className="v5-list">
        {rows.length
          ? rows.map((item) => (
            <Card key={item.id} className="v5-row">
              <div>
                <strong>{item.title || item.id}</strong>
                <Text size={200}>{item.summary || item.problem || item.quality_decision || T.waitingContent}</Text>
              </div>
              <div className="v5-actions">
                <Badge>{item.status ? label(item.status) : title}</Badge>
                {tab === 'opportunities' && item.status === 'proposed' ? (
                  <Button size="small" disabled={busyId === item.id} onClick={() => runAction(item.id, () => api.submitOpportunity(projectId, item.id))}>{T.submitApproval}</Button>
                ) : null}
                {tab === 'opportunities' && item.status === 'pending_approval' ? (
                  <>
                    <Button size="small" appearance="primary" disabled={busyId === item.id} onClick={() => runAction(item.id, () => api.approveOpportunity(projectId, item.id))}>{T.approve}</Button>
                    <Button size="small" disabled={busyId === item.id} onClick={() => runAction(item.id, () => api.rejectOpportunity(projectId, item.id))}>{T.reject}</Button>
                  </>
                ) : null}
                {tab === 'opportunities' && item.status === 'approved' ? (
                  <Button
                    size="small"
                    appearance="primary"
                    disabled={busyId === item.id}
                    onClick={() => runAction(item.id, async () => {
                      await api.createPrd(projectId, { opportunity_id: item.id, title: item.title || 'PRD', markdown: `# ${item.title || 'PRD'}\n\n${item.problem || ''}` });
                      setTab('prds');
                    })}
                  >
                    {T.createPrdFromOpp}
                  </Button>
                ) : null}
                {tab === 'prds' && (item.status === 'draft' || item.status === 'quality_checked') ? (
                  <Button size="small" disabled={busyId === item.id} onClick={() => runAction(item.id, () => api.assessPrd(projectId, item.id))}>{T.assessQuality}</Button>
                ) : null}
                {tab === 'prds' && item.status === 'quality_checked' && (!item.quality_decision || item.quality_decision === 'pass') ? (
                  <Button size="small" appearance="primary" disabled={busyId === item.id} onClick={() => runAction(item.id, () => api.approvePrd(projectId, item.id))}>{T.approve}</Button>
                ) : null}
                {tab === 'prds' && item.status === 'quality_checked' && item.quality_decision && item.quality_decision !== 'pass' ? (
                  <Button size="small" disabled={busyId === item.id} onClick={() => runAction(item.id, () => api.waivePrd(projectId, item.id))}>{T.waiveQuality}</Button>
                ) : null}
                {tab === 'prds' && (item.status === 'approved' || item.status === 'waived') ? (
                  <Button size="small" appearance="primary" disabled={busyId === item.id} onClick={() => runAction(item.id, () => api.readyPrd(projectId, item.id))}>{T.markReady}</Button>
                ) : null}
              </div>
            </Card>
          ))
          : <Empty>{T.noItems(title)}</Empty>}
      </div>
      <InsightDialog
        open={insightOpen}
        onOpenChange={setInsightOpen}
        projectId={projectId}
        onSaved={() => { toast(T.insightSaved, 'success'); refresh(); }}
      />
      <OpportunityDialog
        open={opportunityOpen}
        onOpenChange={setOpportunityOpen}
        projectId={projectId}
        onSaved={() => { toast(T.opportunitySaved, 'success'); refresh(); }}
      />
    </>
  );
}

function InsightDialog({ open, onOpenChange, projectId, onSaved }) {
  const [title, setTitle] = useState('');
  const [summary, setSummary] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  async function save(event) {
    event.preventDefault();
    if (!title.trim()) return;
    setSaving(true);
    setError('');
    try {
      await api.createInsight(projectId, { title: title.trim(), summary: summary.trim() });
      setTitle('');
      setSummary('');
      onSaved();
      onOpenChange(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <Dialog open={open} onOpenChange={(_, data) => onOpenChange(data.open)}>
      <DialogSurface>
        <form onSubmit={save}>
          <DialogBody>
            <DialogTitle>{T.addInsight}</DialogTitle>
            <DialogContent className="v5-dialog">
              <Field label={T.insightTitle} required><Input value={title} onChange={(_, data) => setTitle(data.value)} /></Field>
              <Field label={T.insightSummary}><Textarea value={summary} onChange={(_, data) => setSummary(data.value)} resize="vertical" /></Field>
              {error ? <Text role="alert" className="v5-inline-error">{error}</Text> : null}
            </DialogContent>
            <DialogActions>
              <Button appearance="secondary" onClick={() => onOpenChange(false)}>{T.cancel}</Button>
              <Button appearance="primary" type="submit" disabled={!title.trim() || saving}>{saving ? T.saving : T.addInsight}</Button>
            </DialogActions>
          </DialogBody>
        </form>
      </DialogSurface>
    </Dialog>
  );
}

function OpportunityDialog({ open, onOpenChange, projectId, onSaved }) {
  const [title, setTitle] = useState('');
  const [problem, setProblem] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  async function save(event) {
    event.preventDefault();
    if (!title.trim()) return;
    setSaving(true);
    setError('');
    try {
      await api.createOpportunity(projectId, { title: title.trim(), problem: problem.trim() });
      setTitle('');
      setProblem('');
      onSaved();
      onOpenChange(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <Dialog open={open} onOpenChange={(_, data) => onOpenChange(data.open)}>
      <DialogSurface>
        <form onSubmit={save}>
          <DialogBody>
            <DialogTitle>{T.addOpportunity}</DialogTitle>
            <DialogContent className="v5-dialog">
              <Field label={T.opportunityTitle} required><Input value={title} onChange={(_, data) => setTitle(data.value)} /></Field>
              <Field label={T.opportunityProblem}><Textarea value={problem} onChange={(_, data) => setProblem(data.value)} resize="vertical" /></Field>
              {error ? <Text role="alert" className="v5-inline-error">{error}</Text> : null}
            </DialogContent>
            <DialogActions>
              <Button appearance="secondary" onClick={() => onOpenChange(false)}>{T.cancel}</Button>
              <Button appearance="primary" type="submit" disabled={!title.trim() || saving}>{saving ? T.saving : T.addOpportunity}</Button>
            </DialogActions>
          </DialogBody>
        </form>
      </DialogSurface>
    </Dialog>
  );
}

function Deliveries({ projectId, awaitingProject, toast }) {
  const [params] = useSearchParams();
  const runId = params.get('run_id') || '';
  const [busyId, setBusyId] = useState('');
  const load = useCallback(async (id) => {
    const [deliveries, prds] = await Promise.all([api.listDeliveries(id), api.listPrds(id)]);
    return {
      deliveries: deliveries.deliveries || [],
      readyPrds: (prds.prd_versions || []).filter((item) => item.status === 'ready_for_delivery'),
    };
  }, []);
  const { error, data, refresh } = useData(projectId, load);
  if (awaitingProject) return <NeedProject loading />;
  if (!projectId) return <NeedProject />;
  if (!data && !error) return <Loading label={T.loadingDeliveries} />;
  if (error) return <ErrorState error={error} retry={refresh} />;

  async function exportPrd(prd) {
    setBusyId(prd.id);
    try {
      await api.createDelivery(projectId, { prd_version_id: prd.id });
      toast(T.deliverySaved, 'success');
      refresh();
    } catch (err) {
      toast(err.message || T.confirmFail, 'error');
    } finally {
      setBusyId('');
    }
  }

  return (
    <>
      <Header title={T.deliveries} action={<Button appearance="secondary" onClick={refresh}>{T.refresh}</Button>} />
      {runId ? (
        <>
          <h2 className="v5-subtitle">{T.reviewProgress}</h2>
          <WorkspaceReviewProgress projectId={projectId} runId={runId} />
        </>
      ) : null}
      <h2 className="v5-subtitle">{T.exportDelivery}</h2>
      <div className="v5-list">
        {data.readyPrds.length
          ? data.readyPrds.map((item) => (
            <Card key={item.id} className="v5-row">
              <div>
                <strong>{item.title || item.id}</strong>
                <Text size={200}>{label(item.status)}</Text>
              </div>
              <Button appearance="primary" disabled={busyId === item.id} onClick={() => exportPrd(item)}>{T.exportDelivery}</Button>
            </Card>
          ))
          : <Empty>{T.noReadyPrd}</Empty>}
      </div>
      <h2 className="v5-subtitle">{T.deliveryRecord}</h2>
      <div className="v5-list">
        {data.deliveries.length
          ? data.deliveries.map((item) => (
            <Card key={item.id} className="v5-row">
              <div>
                <strong>{item.target_kind || T.deliveryRecord}</strong>
                <Text size={200}>{item.failure_reason || formatDateTime(item.updated_at)}</Text>
              </div>
              <Badge color={color(item.status)}>{label(item.status)}</Badge>
            </Card>
          ))
          : <Empty>{T.noDeliveries}</Empty>}
      </div>
    </>
  );
}

function Confirmations({ projectId, awaitingProject, toast }) {
  const navigate = useNavigate();
  const { error, data, refresh } = useData(projectId, useCallback((id) => api.listPendingTasks(id), []));
  if (awaitingProject) return <NeedProject loading />;
  if (!projectId) return <NeedProject />;
  if (!data && !error) return <Loading label={T.loadingConfirmations} />;
  if (error) return <ErrorState error={error} retry={refresh} />;
  return (
    <>
      <Header title={T.confirmations} action={<Button appearance="secondary" onClick={refresh}>{T.refresh}</Button>} />
      <div className="v5-list">
        {data.items.length
          ? data.items.map((item) => {
            const runId = item.details?.result?.run_id || '';
            return (
              <Card className="v5-row" key={item.id}>
                <div>
                  <strong>{item.title || item.kind}</strong>
                  <Text size={200}>{item.details?.next_step || T.nextStepFallback}</Text>
                </div>
                <div className="v5-actions">
                  <TaskActions taskId={item.id} onResolved={refresh} toast={toast} />
                  {runId ? (
                    <Button
                      size="small"
                      onClick={() => navigate(`/deliveries?project_id=${encodeURIComponent(projectId)}&run_id=${encodeURIComponent(runId)}`)}
                    >
                      {T.viewReviewProgress}
                    </Button>
                  ) : null}
                </div>
              </Card>
            );
          })
          : <Empty>{T.noConfirmations}</Empty>}
      </div>
    </>
  );
}

function Settings() {
  const [providerInfo, setProviderInfo] = useState({ loading: true, error: '', connections: [], masterKey: false });
  const loadProviders = useCallback(async () => {
    setProviderInfo((v) => ({ ...v, loading: true, error: '' }));
    try {
      const result = await api.listProviderConnections();
      setProviderInfo({
        loading: false,
        error: '',
        connections: result.connections || [],
        masterKey: Boolean(result.master_key_configured),
      });
    } catch (error) {
      setProviderInfo((v) => ({ ...v, loading: false, error: error.message }));
    }
  }, []);
  useEffect(() => { loadProviders(); }, [loadProviders]);
  return (
    <>
      <Header title={T.settings} action={<Button appearance="secondary" onClick={loadProviders}>{T.refresh}</Button>} />
      <Card className="v5-item">
        <Text>{T.desktopHint}</Text>
        <Text size={200}>{T.feishuSetupHint}</Text>
      </Card>
      <Card className="v5-item">
        <strong>{T.providerStatus}</strong>
        {providerInfo.loading ? <Spinner size="tiny" label={T.refresh} /> : null}
        {providerInfo.error ? <Text role="alert" className="v5-inline-error">{providerInfo.error}</Text> : null}
        {!providerInfo.loading && !providerInfo.error ? (
          <>
            <Text>{providerInfo.masterKey ? T.masterKeyOk : T.masterKeyMissing}</Text>
            <Text>{T.connectionCount(providerInfo.connections.length)}</Text>
          </>
        ) : null}
      </Card>
    </>
  );
}

function ProjectPicker({ projects, projectId, loading, onChange }) {
  const [queryText, setQueryText] = useState('');
  useEffect(() => {
    setQueryText('');
  }, [projectId]);
  const options = filterProjects(projects, queryText);
  const selected = projects.find((item) => item.id === projectId);
  const searching = Boolean(String(queryText || '').trim());
  return (
    <div className="v5-project-picker">
      <Input
        aria-label={T.searchProjects}
        placeholder={T.searchProjects}
        value={queryText}
        onChange={(_, data) => setQueryText(data.value)}
        disabled={loading}
      />
      <select
        aria-label={T.choose}
        value={projectId}
        disabled={loading}
        onChange={(event) => {
          setQueryText('');
          onChange(event.target.value);
        }}
      >
        <option value="">{loading ? T.loadingProjects : T.choose}</option>
        {selected && !options.some((item) => item.id === selected.id) ? (
          <option value={selected.id}>{projectLabel(selected)}</option>
        ) : null}
        {options.map((item) => (
          <option value={item.id} key={item.id}>{projectLabel(item)}</option>
        ))}
      </select>
      <Text size={200} className="v5-project-hint">
        {T.projectCount(projects.length, options.length, searching)}
      </Text>
    </div>
  );
}

function Shell() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [projects, setProjects] = useState([]);
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [projectError, setProjectError] = useState('');
  const [agentOpen, setAgentOpen] = useState(false);
  const [projectOpen, setProjectOpen] = useState(false);
  const [dark, setDark] = useState(() => (
    localStorage.getItem('pm-pal-theme') === 'dark'
    || (!localStorage.getItem('pm-pal-theme') && window.matchMedia('(prefers-color-scheme: dark)').matches)
  ));
  const projectId = params.get('project_id') || '';
  const conversationId = params.get('conversation_id') || '';
  const toasterId = useId('toast');
  const { dispatchToast } = useToastController(toasterId);

  const toast = useCallback((message, intent = 'info') => {
    dispatchToast(<Toast>{message}</Toast>, { intent });
  }, [dispatchToast]);

  const patchParams = useCallback((updates) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      Object.entries(updates).forEach(([key, value]) => {
        if (value === undefined || value === null || value === '') next.delete(key);
        else next.set(key, value);
      });
      return next;
    }, { replace: true });
  }, [setParams]);

  const loadProjects = useCallback(async () => {
    setProjectsLoading(true);
    try {
      const result = await api.listProjects();
      const items = rankProjects(result.projects || result || []);
      setProjects(items);
      setProjectError('');
    } catch (error) {
      setProjectError(error.message);
    } finally {
      setProjectsLoading(false);
    }
  }, []);

  useEffect(() => { loadProjects(); }, [loadProjects]);

  useEffect(() => {
    if (projectsLoading || projectError || projectId || !projects.length) return;
    const remembered = localStorage.getItem(LAST_PROJECT_KEY) || '';
    const preferred = projects.find((item) => item.id === remembered) || projects.find((item) => !isWeakProjectName(item.name)) || projects[0];
    if (preferred) patchParams({ project_id: preferred.id });
  }, [projectsLoading, projectError, projectId, projects, patchParams]);

  useEffect(() => {
    if (projectId) localStorage.setItem(LAST_PROJECT_KEY, projectId);
  }, [projectId]);

  useEffect(() => {
    localStorage.setItem('pm-pal-theme', dark ? 'dark' : 'light');
    document.documentElement.style.colorScheme = dark ? 'dark' : 'light';
  }, [dark]);

  const project = projects.find((item) => item.id === projectId);

  const awaitingProject = projectsLoading && !projectId;

  const onAgentCreated = (result) => {
    const id = result?.conversation?.id;
    toast(T.taskCreated, 'success');
    if (id) {
      patchParams({ project_id: projectId, conversation_id: id });
      navigate(`/workspace?project_id=${encodeURIComponent(projectId)}&conversation_id=${encodeURIComponent(id)}`);
    } else {
      navigate(`/workspace?project_id=${encodeURIComponent(projectId)}`);
    }
  };

  const settingsHref = projectId ? `/settings?project_id=${encodeURIComponent(projectId)}` : '/settings';

  return (
    <FluentProvider theme={dark ? webDarkTheme : webLightTheme}>
      <div className="v5-shell">
        <aside className="v5-rail">
          <div className="v5-brand">P</div>
          <nav aria-label={T.navigation}>
            {NAV.map(([to, name, Icon]) => (
              <NavLink key={to} to={`${to}${projectId ? `?project_id=${projectId}` : ''}`}>
                <Icon size={19} />
                <span>{name}</span>
              </NavLink>
            ))}
          </nav>
          <Button
            appearance="subtle"
            aria-label={T.theme}
            icon={dark ? <IconSun size={18} /> : <IconMoon size={18} />}
            onClick={() => setDark((value) => !value)}
          />
        </aside>
        <main className="v5-main">
          <header className="v5-topbar">
            <div>
              <Text size={200}>{T.current}</Text>
              <strong>{project ? projectLabel(project) : (projectsLoading ? T.loadingProjects : T.choose)}</strong>
            </div>
            <ProjectPicker
              projects={projects}
              projectId={projectId}
              loading={projectsLoading}
              onChange={(id) => patchParams({ project_id: id, conversation_id: '' })}
            />
            <Button appearance="secondary" icon={<IconPlus size={16} />} onClick={() => setProjectOpen(true)}>{T.create}</Button>
            <Button appearance="primary" icon={<IconRobot size={16} />} disabled={!projectId} onClick={() => setAgentOpen(true)}>{T.ask}</Button>
          </header>
          {projectError ? (
            <div className="v5-banner">
              <ErrorState
                error={projectError}
                retry={loadProjects}
                action={<Button appearance="primary" onClick={() => navigate(settingsHref)}>{T.goSettings}</Button>}
              />
            </div>
          ) : null}
          <section className="v5-content">
            <Routes>
              <Route
                path="/workspace"
                element={(
                    <Workspace
                      projectId={projectId}
                      awaitingProject={awaitingProject}
                      openAgent={() => setAgentOpen(true)}
                      conversationId={conversationId}
                      onOpenConversation={(id) => {
                        patchParams({ conversation_id: id });
                        navigate(`/workspace?project_id=${encodeURIComponent(projectId)}&conversation_id=${encodeURIComponent(id)}`);
                      }}
                      onCloseConversation={() => {
                        patchParams({ conversation_id: '' });
                        navigate(`/workspace?project_id=${encodeURIComponent(projectId)}`);
                      }}
                      toast={toast}
                    />
                  )}
                />
                <Route path="/materials" element={<Materials projectId={projectId} awaitingProject={awaitingProject} toast={toast} onReviewStarted={onAgentCreated} />} />
                <Route path="/decisions" element={<Decisions projectId={projectId} awaitingProject={awaitingProject} toast={toast} />} />
                <Route path="/deliveries" element={<Deliveries projectId={projectId} awaitingProject={awaitingProject} toast={toast} />} />
                <Route path="/confirmations" element={<Confirmations projectId={projectId} awaitingProject={awaitingProject} toast={toast} />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/run/:runId" element={<RunRedirect />} />
              <Route path="/projects/:projectId/reviews/:runId" element={<LegacyProjectReviewRedirect />} />
              <Route path="*" element={<Navigate to={`/workspace${projectId ? `?project_id=${projectId}` : ''}`} replace />} />
            </Routes>
          </section>
        </main>
      </div>
      <AgentDialog open={agentOpen} onOpenChange={setAgentOpen} projectId={projectId} onCreated={onAgentCreated} />
      <ProjectDialog
        open={projectOpen}
        onOpenChange={setProjectOpen}
        onCreated={(item) => {
          setProjects((items) => rankProjects([item, ...items]));
          patchParams({ project_id: item.id, conversation_id: '' });
        }}
      />
      <Toaster toasterId={toasterId} />
    </FluentProvider>
  );
}

export default function App() {
  return <Shell />;
}
