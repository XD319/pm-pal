import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders, screen, userEvent, waitFor } from '../test/utils';
import PmConsolePage from '../pages/PmConsolePage';

vi.mock('../api', () => ({
  runPmPipeline: vi.fn(),
}));

import { runPmPipeline } from '../api';

describe('PmConsolePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('runs the PM pipeline and renders evidence chain', async () => {
    runPmPipeline.mockResolvedValue({
      pipeline_id: 'pipe-1',
      status: 'completed',
      stage: 'complete',
      feedback_ids: ['fb-1'],
      insight_ids: ['ins-1'],
      opportunity_id: 'opp-1',
      prd_id: 'prd-1',
      review_run_id: '',
      insights: [
        {
          id: 'ins-1',
          title: 'Auth friction',
          summary: 'Login drop-off',
          source_refs: ['feedback:fb-1'],
        },
      ],
      opportunity: {
        title: 'Simplify auth',
        problem: 'Users abandon login',
        users: 'New users',
        value: 'Activation',
        open_questions: ['Which IdP?'],
      },
      prd: {
        title: 'Auth PRD',
        markdown: '# Goals\n- Reduce drop-off',
        evidence_refs: ['feedback:fb-1', 'insight:ins-1'],
      },
    });

    const user = userEvent.setup();
    renderWithProviders(<PmConsolePage />);

    await user.click(screen.getByRole('button', { name: /run pm pipeline/i }));

    await waitFor(() => {
      expect(screen.getByText('pipe-1')).toBeInTheDocument();
    });
    expect(runPmPipeline).toHaveBeenCalled();
    expect(screen.getByText('Auth friction')).toBeInTheDocument();
    expect(screen.getByText(/Feedback IDs: fb-1/)).toBeInTheDocument();
    expect(screen.getByText(/# Goals/)).toBeInTheDocument();
  });
});
