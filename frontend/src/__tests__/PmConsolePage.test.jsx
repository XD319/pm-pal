import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders, screen, userEvent, waitFor } from '../test/utils';
import PmConsolePage from '../pages/PmConsolePage';

vi.mock('../api', () => ({
  listDecisionSources: vi.fn(),
  listDecisionEvidence: vi.fn(),
  listDecisionInsights: vi.fn(),
  listDecisionOpportunities: vi.fn(),
  listDecisionPrdVersions: vi.fn(),
  listDecisionDeliveries: vi.fn(),
  fetchPilotMetrics: vi.fn(),
  refreshDecisionSource: vi.fn(),
  getDecisionSyncStatus: vi.fn(),
  submitDecisionOpportunity: vi.fn(),
  approveDecisionOpportunity: vi.fn(),
  rejectDecisionOpportunity: vi.fn(),
  assessDecisionPrd: vi.fn(),
  approveDecisionPrd: vi.fn(),
  waiveDecisionPrd: vi.fn(),
  readyDecisionPrd: vi.fn(),
  exportDecisionPrd: vi.fn(),
  fetchDecisionTrace: vi.fn(),
  runPmPipeline: vi.fn(),
}));

import {
  exportDecisionPrd,
  fetchPilotMetrics,
  listDecisionDeliveries,
  listDecisionEvidence,
  listDecisionInsights,
  listDecisionOpportunities,
  listDecisionPrdVersions,
  listDecisionSources,
  submitDecisionOpportunity,
} from '../api';

describe('PmConsolePage workbench', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listDecisionSources.mockResolvedValue({ sources: [{ id: 'source-1', display_name: 'Inbox', source_type: 'feishu_doc', sync_status: 'succeeded', source_url: 'https://example.feishu.cn/docx/1' }] });
    listDecisionEvidence.mockResolvedValue({ evidence: [{ id: 'evidence-1', summary: 'Offline drafting', content: 'Need offline', author: 'ou_a', updated_at: 't1', source_url: 'https://example.feishu.cn/docx/1' }] });
    listDecisionInsights.mockResolvedValue({ insights: [] });
    listDecisionOpportunities.mockResolvedValue({
      opportunities: [{ id: 'opp-1', title: 'Offline mode', status: 'proposed', updated_at: 't2', audit_id: 'audit-1', source_urls: ['https://example.feishu.cn/docx/1'] }],
    });
    listDecisionPrdVersions.mockResolvedValue({
      prd_versions: [{ id: 'prd-1:v1', title: 'Offline PRD', status: 'ready_for_delivery', quality_decision: 'pass', updated_at: 't3', audit_id: 'audit-2', source_urls: [] }],
    });
    listDecisionDeliveries.mockResolvedValue({
      deliveries: [{ id: 'del-1', target_kind: 'feishu_bitable', status: 'degraded', failure_reason: 'project permission denied', updated_at: 't4', external_url: 'https://feishu.cn/base/1' }],
    });
    fetchPilotMetrics.mockResolvedValue({
      metrics: {
        sync_success_rate: 1,
        insight_with_evidence_rate: 1,
        opportunity_approval_rate: 0.5,
        quality_pass_rate: 1,
        delivery_completion_rate: 1,
        revision_reasons: ['reopened'],
      },
    });
    window.confirm = vi.fn(() => true);
  });

  it('loads four views and shows permission/export degrade states', async () => {
    const user = userEvent.setup();
    renderWithProviders(<PmConsolePage />);

    await waitFor(() => {
      expect(screen.getByText('Offline drafting')).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: '反馈与证据库' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /机会看板/ })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /机会看板/ }));
    expect(await screen.findByText('Offline mode')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '提交审批' })).toBeInTheDocument();

    submitDecisionOpportunity.mockResolvedValue({ status: 'pending_approval' });
    await user.click(screen.getByRole('button', { name: '提交审批' }));
    expect(window.confirm).toHaveBeenCalled();
    expect(submitDecisionOpportunity).toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'PRD 与质量中心' }));
    expect(await screen.findByText('Offline PRD')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '放行' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '豁免' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '交付与决策追溯' }));
    expect(await screen.findByText('已降级：project permission denied')).toBeInTheDocument();
    expect(screen.getByText('试点指标')).toBeInTheDocument();

    exportDecisionPrd.mockResolvedValue({
      status: 'degraded',
      delivery: { status: 'degraded', failure_reason: 'project permission denied' },
    });
    await user.click(screen.getByRole('button', { name: '导出交付包' }));
    expect(exportDecisionPrd).toHaveBeenCalled();
  });

  it('shows load error state', async () => {
    listDecisionSources.mockRejectedValueOnce(new Error('boom'));
    renderWithProviders(<PmConsolePage />);
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
  });
});
