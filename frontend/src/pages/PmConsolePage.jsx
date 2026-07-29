import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  approveDecisionOpportunity,
  approveDecisionPrd,
  assessDecisionPrd,
  exportDecisionPrd,
  fetchDecisionTrace,
  fetchPilotMetrics,
  generateDecisionDrafts,
  confirmDecisionEvidence,
  getDecisionSyncStatus,
  listDecisionDeliveries,
  listDecisionEvidence,
  listDecisionInsights,
  listDecisionOpportunities,
  listDecisionPrdVersions,
  listDecisionSources,
  readyDecisionPrd,
  refreshDecisionSource,
  rejectDecisionOpportunity,
  runPmPipeline,
  submitDecisionOpportunity,
  waiveDecisionPrd,
} from '../api';
import PanelErrorBoundary from '../components/PanelErrorBoundary';
import { useToast } from '../components/ToastProvider';
import { formatApiError } from '../utils/errors';
import { formatSourceType, formatStatus } from '../utils/presentation';

const VIEWS = [
  { id: 'evidence', label: '反馈与证据库' },
  { id: 'opportunities', label: '机会看板' },
  { id: 'quality', label: 'PRD 与质量中心' },
  { id: 'delivery', label: '交付与决策追溯' },
];

function readOpenId(searchParams) {
  return String(searchParams.get('open_id') || searchParams.get('user_id') || '').trim();
}

function confirmAction(message) {
  return window.confirm(message);
}

function StatusPill({ value }) {
  return <span className="status-pill">{formatStatus(value)}</span>;
}

function ArtifactMeta({ owner, time, status, quality, reason, links }) {
  return (
    <dl className="meta-list artifact-meta">
      <div><dt>责任人</dt><dd>{owner || '—'}</dd></div>
      <div><dt>时间</dt><dd>{time || '—'}</dd></div>
      <div><dt>状态</dt><dd><StatusPill value={status} /></dd></div>
      {quality ? <div><dt>质量结论</dt><dd>{quality}</dd></div> : null}
      {reason ? <div><dt>审计原因</dt><dd>{reason}</dd></div> : null}
      {links?.length ? (
        <div>
          <dt>飞书原文</dt>
          <dd>
            {links.map((link) => (
              <a key={link} href={link} target="_blank" rel="noreferrer">深链接</a>
            ))}
          </dd>
        </div>
      ) : null}
    </dl>
  );
}

function PmConsolePage() {
  const { showToast } = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const view = VIEWS.some((item) => item.id === searchParams.get('view'))
    ? searchParams.get('view')
    : 'evidence';
  const openId = readOpenId(searchParams);
  const productId = String(searchParams.get('product_id') || 'p-1').trim() || 'p-1';

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [sources, setSources] = useState([]);
  const [evidence, setEvidence] = useState([]);
  const [insights, setInsights] = useState([]);
  const [opportunities, setOpportunities] = useState([]);
  const [prdVersions, setPrdVersions] = useState([]);
  const [deliveries, setDeliveries] = useState([]);
  const [trace, setTrace] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [traceRoot, setTraceRoot] = useState('');
  const [exportTarget, setExportTarget] = useState({ app_token: '', table_id: '', project_key: '' });
  const [exportHint, setExportHint] = useState('');
  const [legacyResult, setLegacyResult] = useState(null);

  const setView = useCallback((nextView) => {
    const next = new URLSearchParams(searchParams);
    next.set('view', nextView);
    next.set('product_id', productId);
    setSearchParams(next, { replace: true });
  }, [productId, searchParams, setSearchParams]);

  const loadWorkbench = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [sourcePayload, evidencePayload, insightPayload, opportunityPayload, prdPayload, deliveryPayload, metricPayload] = await Promise.all([
        listDecisionSources(productId),
        listDecisionEvidence({ productId }),
        listDecisionInsights(productId),
        listDecisionOpportunities(productId),
        listDecisionPrdVersions(productId),
        listDecisionDeliveries({ productId }),
        fetchPilotMetrics(productId).catch(() => ({ metrics: null })),
      ]);
      setSources(sourcePayload.sources || []);
      setEvidence(evidencePayload.evidence || []);
      setInsights(insightPayload.insights || []);
      setOpportunities(opportunityPayload.opportunities || []);
      setPrdVersions(prdPayload.prd_versions || []);
      setDeliveries(deliveryPayload.deliveries || []);
      setMetrics(metricPayload.metrics || null);
    } catch (err) {
      const message = formatApiError(err, '工作台加载失败');
      setError(message);
      showToast(message, 'error');
    } finally {
      setLoading(false);
    }
  }, [productId, showToast]);

  useEffect(() => {
    loadWorkbench();
  }, [loadWorkbench]);

  const pendingApprovals = useMemo(
    () => opportunities.filter((item) => item.status === 'pending_approval'),
    [opportunities],
  );

  async function handleRefreshSource(sourceId) {
    try {
      await refreshDecisionSource(sourceId);
      const status = await getDecisionSyncStatus(sourceId);
      showToast(`同步状态：${status.sync_status}`, 'success');
      await loadWorkbench();
    } catch (err) {
      showToast(formatApiError(err, '来源刷新失败'), 'error');
    }
  }

  async function handleOpportunityAction(opportunity, action) {
    const labels = {
      submit: '确认提交该机会进入审批？门禁由服务端校验。',
      approve: '确认以产品负责人身份批准该机会？',
      reject: '确认拒绝该机会？',
    };
    if (!confirmAction(labels[action])) return;
    const reason = action === 'reject'
      ? window.prompt('请填写拒绝原因', '') || ''
      : '';
    if (action === 'reject' && !reason.trim()) {
      showToast('拒绝原因必填', 'error');
      return;
    }
    try {
      const payload = action === 'approve'
        ? { actor_open_id: openId || 'ou_owner', reason }
        : { actor: openId || 'ou_pm', actor_open_id: openId || 'ou_owner', reason };
      if (action === 'submit') await submitDecisionOpportunity(opportunity.id, payload);
      if (action === 'approve') await approveDecisionOpportunity(opportunity.id, payload);
      if (action === 'reject') await rejectDecisionOpportunity(opportunity.id, payload);
      showToast(`机会已${action}`, 'success');
      await loadWorkbench();
    } catch (err) {
      showToast(formatApiError(err, '机会操作失败'), 'error');
    }
  }

  async function handlePrdAction(prd, action) {
    const prompts = {
      assess: '确认请求质量评估？',
      approve: '确认放行该质量通过的 PRD？',
      waive: '确认豁免该 PRD？需要填写原因。',
      ready: '确认标记为可交付？',
    };
    if (!confirmAction(prompts[action])) return;
    let reason = '';
    if (action === 'waive') {
      reason = window.prompt('请填写豁免原因', '') || '';
      if (!reason.trim()) {
        showToast('豁免原因必填', 'error');
        return;
      }
    }
    try {
      const payload = { actor_open_id: openId || 'ou_owner', reason };
      if (action === 'assess') await assessDecisionPrd(prd.id, payload);
      if (action === 'approve') await approveDecisionPrd(prd.id, payload);
      if (action === 'waive') await waiveDecisionPrd(prd.id, payload);
      if (action === 'ready') await readyDecisionPrd(prd.id, payload);
      showToast(`PRD 已${action}`, 'success');
      await loadWorkbench();
    } catch (err) {
      showToast(formatApiError(err, 'PRD 操作失败'), 'error');
    }
  }

  async function handleExport(prd) {
    if (!confirmAction('确认导出已批准交付包？仅 ready_for_delivery 可由服务端放行。')) return;
    setExportHint('');
    try {
      const result = await exportDecisionPrd(prd.id, {
        actor_open_id: openId || 'ou_owner',
        app_token: exportTarget.app_token,
        table_id: exportTarget.table_id,
        project_key: exportTarget.project_key,
        enable_project: Boolean(exportTarget.project_key),
      });
      if (result.delivery?.status === 'degraded') {
        setExportHint(`飞书项目失败，已降级到多维表格：${result.delivery.failure_reason || 'permission/mapping'}`);
      }
      showToast(`导出状态：${result.status || result.delivery?.status}`, 'success');
      await loadWorkbench();
    } catch (err) {
      showToast(formatApiError(err, '导出失败'), 'error');
    }
  }

  async function handleTrace() {
    if (!traceRoot.trim()) return;
    try {
      setTrace(await fetchDecisionTrace(traceRoot.trim()));
    } catch (err) {
      setTrace(null);
      showToast(formatApiError(err, '追溯失败'), 'error');
    }
  }

  async function handleConfirmEvidence(evidenceId) {
    try {
      await confirmDecisionEvidence(evidenceId);
      showToast('证据已确认，可用于生成机会草稿。', 'success');
      await loadWorkbench();
    } catch (err) {
      showToast(formatApiError(err, '确认资料失败'), 'error');
    }
  }
  async function handleGenerateDrafts() {
    try {
      const payload = await generateDecisionDrafts({ product_id: productId, actor: openId || 'ou_pm' });
      showToast(`已生成机会草稿： ${payload.opportunity?.id || payload.job_id}`, 'success');
      setView('opportunities');
      await loadWorkbench();
    } catch (err) {
      showToast(formatApiError(err, '请先确认至少一条有效资料'), 'error');
    }
  }

  return (
    <section className="page-shell pm-console workbench" aria-labelledby="workbench-title">
      <header className="page-header">
        <div>
          <p className="eyebrow">Decision Workbench</p>
          <h1 id="workbench-title">产品决策工作台</h1>
          <p className="page-lead">
            四个固定视图覆盖证据、机会、质量与交付。审批/豁免均需显式确认，门禁由服务端判定。
          </p>
        </div>
      </header>

      <div className="panel workbench-toolbar">
        <label className="field">
          <span>产品 ID</span>
          <input
            value={productId}
            onChange={(event) => {
              const next = new URLSearchParams(searchParams);
              next.set('product_id', event.target.value);
              setSearchParams(next, { replace: true });
            }}
          />
        </label>
        <button type="button" className="ghost-button" onClick={loadWorkbench} disabled={loading}>
          {loading ? '刷新中…' : '刷新'}
        </button>
      </div>

      <nav className="workbench-tabs" aria-label="Workbench views">
        {VIEWS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`workbench-tab${view === item.id ? ' workbench-tab-active' : ''}`}
            onClick={() => setView(item.id)}
          >
            {item.label}
            {item.id === 'opportunities' && pendingApprovals.length ? ` (${pendingApprovals.length})` : ''}
          </button>
        ))}
      </nav>

      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {loading && !evidence.length && !opportunities.length ? <p className="empty-copy">加载工作台…</p> : null}

      {view === 'evidence' ? (
        <div className="workspace-grid pm-console-grid">
          <PanelErrorBoundary title="Evidence sources">
            <div className="panel">
              <div className="panel-header"><h2>证据来源</h2><p>{sources.length} 个来源</p></div>
              <ul className="stack-list">
                {sources.map((source) => (
                  <li key={source.id}>
                    <strong>{source.display_name || source.id}</strong>
                    <p>{formatSourceType(source.source_type)} · <StatusPill value={source.sync_status} /></p>
                    <small>{source.last_error || source.source_url || '—'}</small>
                    <div className="action-row">
                      <button type="button" className="ghost-button" onClick={() => handleRefreshSource(source.id)}>手动刷新</button>
                    </div>
                  </li>
                ))}
                {!sources.length ? <li className="empty-copy">暂无来源，可通过 API/机器人登记。</li> : null}
              </ul>
            </div>
          </PanelErrorBoundary>
          <PanelErrorBoundary title="Evidence library">
            <div className="panel">
              <div className="panel-header"><h2>证据库</h2><p>{evidence.length} 条</p></div>
              <ul className="stack-list">
                {evidence.map((item) => (
                  <li key={item.id}>
                    <strong>{item.summary || item.content.slice(0, 80)}</strong>
                    <ArtifactMeta
                      owner={item.author}
                      time={item.updated_at || item.created_at}
                      status={item.confirmed ? 'confirmed' : 'synced'}
                      links={item.source_url ? [item.source_url] : []}
                    />
                  </li>
                ))}
                {!evidence.length ? <li className="empty-copy">暂无证据。</li> : null}
              </ul>
              <div className="action-row">
                <button type="button" className="ghost-button" onClick={handleGenerateDrafts}>生成基于证据的机会草稿</button>
              </div>
              {legacyResult ? <p className="empty-copy">兼容结果：{legacyResult.pipeline_id}</p> : null}
            </div>
          </PanelErrorBoundary>
          <PanelErrorBoundary title="Insights">
            <div className="panel">
              <div className="panel-header"><h2>洞察</h2><p>{insights.length}</p></div>
              <ul className="stack-list">
                {insights.map((item) => (
                  <li key={item.id}>
                    <strong>{item.title}</strong>
                    <ArtifactMeta
                      owner={item.metadata?.actor}
                      time={item.updated_at}
                      status={`v${item.version}`}
                      reason={item.audit_id}
                      links={item.source_urls || []}
                    />
                  </li>
                ))}
              </ul>
            </div>
          </PanelErrorBoundary>
        </div>
      ) : null}

      {view === 'opportunities' ? (
        <PanelErrorBoundary title="Opportunity board">
          <div className="panel">
            <div className="panel-header"><h2>机会看板</h2><p>{opportunities.length} 个候选</p></div>
            <ul className="stack-list">
              {opportunities.map((item) => (
                <li key={item.id}>
                  <strong>{item.title}</strong>
                  <ArtifactMeta
                    owner={item.metadata?.actor}
                    time={item.updated_at}
                    status={item.status}
                    reason={item.audit_id}
                    links={item.source_urls || []}
                  />
                  <div className="action-row">
                    {item.status === 'proposed' ? (
                      <button type="button" className="primary-button" onClick={() => handleOpportunityAction(item, 'submit')}>提交审批</button>
                    ) : null}
                    {item.status === 'pending_approval' ? (
                      <button type="button" className="primary-button" onClick={() => handleOpportunityAction(item, 'approve')}>负责人批准</button>
                    ) : null}
                    {item.status === 'proposed' || item.status === 'pending_approval' ? (
                      <button type="button" className="ghost-button" onClick={() => handleOpportunityAction(item, 'reject')}>拒绝</button>
                    ) : null}
                  </div>
                </li>
              ))}
              {!opportunities.length ? <li className="empty-copy">暂无机会。</li> : null}
            </ul>
          </div>
        </PanelErrorBoundary>
      ) : null}

      {view === 'quality' ? (
        <PanelErrorBoundary title="PRD quality center">
          <div className="panel">
            <div className="panel-header"><h2>PRD 与质量中心</h2><p>{prdVersions.length} 个版本</p></div>
            <ul className="stack-list">
              {prdVersions.map((item) => (
                <li key={item.id}>
                  <strong>{item.title}</strong>
                  <ArtifactMeta
                    owner={item.metadata?.owner}
                    time={item.updated_at}
                    status={item.status}
                    quality={item.quality_decision || '未评估'}
                    reason={item.audit_id}
                    links={item.source_urls || []}
                  />
                  <div className="action-row">
                    <button type="button" className="ghost-button" onClick={() => handlePrdAction(item, 'assess')}>质量评估</button>
                    <button type="button" className="primary-button" onClick={() => handlePrdAction(item, 'approve')}>放行</button>
                    <button type="button" className="ghost-button" onClick={() => handlePrdAction(item, 'waive')}>豁免</button>
                    <button type="button" className="ghost-button" onClick={() => handlePrdAction(item, 'ready')}>可交付</button>
                  </div>
                </li>
              ))}
              {!prdVersions.length ? <li className="empty-copy">暂无正式 PRD 版本。</li> : null}
            </ul>
          </div>
        </PanelErrorBoundary>
      ) : null}

      {view === 'delivery' ? (
        <div className="workspace-grid pm-console-results">
          <PanelErrorBoundary title="Delivery exports">
            <div className="panel">
              <div className="panel-header"><h2>交付导出</h2><p>{deliveries.length}</p></div>
              <div className="field-grid">
                <label className="field"><span>Bitable app_token</span><input value={exportTarget.app_token} onChange={(e) => setExportTarget((c) => ({ ...c, app_token: e.target.value }))} /></label>
                <label className="field"><span>table_id</span><input value={exportTarget.table_id} onChange={(e) => setExportTarget((c) => ({ ...c, table_id: e.target.value }))} /></label>
                <label className="field"><span>project_key（可选）</span><input value={exportTarget.project_key} onChange={(e) => setExportTarget((c) => ({ ...c, project_key: e.target.value }))} /></label>
              </div>
              {exportHint ? <p className="form-error" role="status">{exportHint}</p> : null}
              <ul className="stack-list">
                {prdVersions.filter((item) => item.status === 'ready_for_delivery').map((item) => (
                  <li key={item.id}>
                    <strong>{item.title}</strong>
                    <div className="action-row">
                      <button type="button" className="primary-button" onClick={() => handleExport(item)}>导出交付包</button>
                    </div>
                  </li>
                ))}
              </ul>
              <ul className="stack-list">
                {deliveries.map((item) => (
                  <li key={item.id}>
                    <strong>{item.target_kind}</strong>
                    <ArtifactMeta
                      owner={item.metadata?.actor}
                      time={item.updated_at}
                      status={item.status}
                      reason={item.failure_reason || item.audit_id}
                      links={item.external_url ? [item.external_url] : []}
                    />
                    {item.status === 'degraded' ? <p className="form-error">已降级：{item.failure_reason}</p> : null}
                  </li>
                ))}
              </ul>
            </div>
          </PanelErrorBoundary>
          <PanelErrorBoundary title="Decision trace">
            <div className="panel">
              <div className="panel-header"><h2>决策追溯</h2><p>证据 → 洞察 → 机会 → PRD → 交付</p></div>
              <label className="field">
                <span>根 ID</span>
                <input value={traceRoot} onChange={(e) => setTraceRoot(e.target.value)} placeholder="evidence/opportunity/prd id" />
              </label>
              <button type="button" className="ghost-button" onClick={handleTrace}>查询追溯</button>
              {trace ? (
                <ol className="stack-list">
                  {(trace.nodes || []).map((node) => (
                    <li key={`${node.type}-${node.id}`}>{node.type}: {node.label || node.id}</li>
                  ))}
                </ol>
              ) : <p className="empty-copy">输入根 ID 查看链路。</p>}
            </div>
          </PanelErrorBoundary>
          <PanelErrorBoundary title="Pilot metrics">
            <div className="panel">
              <div className="panel-header"><h2>试点指标</h2><p>{productId}</p></div>
              {metrics ? (
                <dl className="meta-list">
                  <div><dt>同步成功率</dt><dd>{metrics.sync_success_rate}</dd></div>
                  <div><dt>带证据洞察比例</dt><dd>{metrics.insight_with_evidence_rate}</dd></div>
                  <div><dt>机会批准率</dt><dd>{metrics.opportunity_approval_rate}</dd></div>
                  <div><dt>质量门禁通过率</dt><dd>{metrics.quality_pass_rate}</dd></div>
                  <div><dt>交付完成率</dt><dd>{metrics.delivery_completion_rate}</dd></div>
                  <div><dt>人工修订原因</dt><dd>{(metrics.revision_reasons || []).join('; ') || '—'}</dd></div>
                </dl>
              ) : <p className="empty-copy">暂无试点指标。</p>}
              <p className="empty-copy">
                飞书深链示例：
                <Link to={`/pm?view=opportunities&product_id=${encodeURIComponent(productId)}&open_id=${encodeURIComponent(openId || 'ou_owner')}`}>
                  待审批机会
                </Link>
              </p>
            </div>
          </PanelErrorBoundary>
        </div>
      ) : null}
    </section>
  );
}

export default PmConsolePage;
