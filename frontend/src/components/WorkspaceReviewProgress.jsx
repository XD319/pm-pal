import { useEffect, useState } from 'react';
import { Badge, Button, Card, Spinner, Text } from '@fluentui/react-components';
import { api } from '../workspaceApi';
import { formatStatus } from '../utils/presentation';
import { formatDateTime } from '../utils/formatters';

const NODE_LABELS = {
  parser: '解析需求',
  planner: '规划任务',
  risk: '风险分析',
  reviewer: '评审综合',
  clarify: '澄清门禁',
  delivery_planning: '交付规划',
  reporter: '生成报告',
  finalize_artifacts: '整理产物',
  parallel_start: '并行启动',
  review_join: '评审汇合',
  route_decider: '路由决策',
};

function nodeLabel(name) {
  const key = String(name || '');
  return NODE_LABELS[key] || key || '等待下一节点';
}

/** Lightweight workspace-v5 review progress panel (no legacy panel CSS). :-) */
export default function WorkspaceReviewProgress({ projectId, runId }) {
  const [state, setState] = useState({ loading: true, error: '', payload: null });

  useEffect(() => {
    if (!projectId || !runId) {
      setState({ loading: false, error: '', payload: null });
      return undefined;
    }
    let cancelled = false;
    let timer = null;

    const poll = async () => {
      try {
        const payload = await api.getReviewStatus(projectId, runId);
        if (cancelled) return;
        setState({ loading: false, error: '', payload });
        const status = String(payload?.status || '');
        if (status === 'running' || status === 'queued') {
          timer = window.setTimeout(poll, 2500);
        }
      } catch (error) {
        if (cancelled) return;
        setState((prev) => ({
          loading: false,
          error: error.message || '无法加载评审进度',
          payload: prev.payload,
        }));
        timer = window.setTimeout(poll, 5000);
      }
    };

    setState({ loading: true, error: '', payload: null });
    poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [projectId, runId]);

  if (!runId) return null;

  const progress = state.payload?.progress || {};
  const status = state.payload?.status || (state.loading ? 'loading' : '');
  const percent = Number(progress.percent || 0);
  const done = status === 'completed' || status === 'succeeded' || status === 'failed';

  return (
    <Card className="v5-item v5-review-progress">
      <div className="v5-row" style={{ padding: 0, border: 'none', background: 'transparent' }}>
        <div>
          <strong>评审进度</strong>
          <Text size={200}>run · {runId}</Text>
        </div>
        <Badge>{formatStatus(status)}</Badge>
      </div>
      {state.loading && !state.payload ? <Spinner size="tiny" label="正在加载评审状态" /> : null}
      {state.error ? <Text role="alert" className="v5-inline-error">{state.error}</Text> : null}
      {state.payload ? (
        <>
          <Text>
            {percent}%
            {' · '}
            {nodeLabel(progress.current_node)}
            {progress.updated_at ? ` · ${formatDateTime(progress.updated_at)}` : ''}
          </Text>
          {progress.error ? <Text role="alert" className="v5-inline-error">{progress.error}</Text> : null}
          {done && status !== 'failed' ? (
            <div className="v5-actions">
              <a href={api.reviewReportPath(projectId, runId, 'md')} target="_blank" rel="noreferrer">
                <Button appearance="primary">打开 Markdown 报告</Button>
              </a>
              <a href={api.reviewReportPath(projectId, runId, 'html')} target="_blank" rel="noreferrer">
                <Button appearance="secondary">打开 HTML 报告</Button>
              </a>
            </div>
          ) : null}
        </>
      ) : null}
    </Card>
  );
}
