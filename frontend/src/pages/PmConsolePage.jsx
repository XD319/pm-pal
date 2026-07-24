import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { runPmPipeline } from '../api';
import PanelErrorBoundary from '../components/PanelErrorBoundary';
import { useToast } from '../components/ToastProvider';
import { formatApiError } from '../utils/errors';

const sampleFeedback = [
  'Login is confusing for first-time enterprise users.',
  'MFA reset flow fails too often and blocks activation.',
  'Checkout crashes on mobile Safari when applying coupons.',
  'Search results feel slow when filtering by category.',
].join('\n');

function splitFeedbackTexts(rawText) {
  return String(rawText || '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function PmConsolePage() {
  const { showToast } = useToast();
  const [feedbackText, setFeedbackText] = useState(sampleFeedback);
  const [productHint, setProductHint] = useState('commerce');
  const [runQualityGate, setRunQualityGate] = useState(false);
  const [submitState, setSubmitState] = useState('idle');
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);

  const feedbackCount = useMemo(
    () => splitFeedbackTexts(feedbackText).length,
    [feedbackText],
  );

  async function handleRunPipeline(event) {
    event.preventDefault();
    const feedbackTexts = splitFeedbackTexts(feedbackText);
    if (!feedbackTexts.length) {
      setError('Paste at least one feedback line.');
      return;
    }

    setSubmitState('running');
    setError('');
    try {
      const payload = await runPmPipeline({
        feedback_texts: feedbackTexts,
        product_hint: productHint,
        source: 'web',
        run_quality_gate: runQualityGate,
      });
      setResult(payload);
      setSubmitState('idle');
      showToast(`PM pipeline completed: ${payload.pipeline_id}`, 'success');
    } catch (err) {
      const message = formatApiError(err, 'PM pipeline failed.');
      setSubmitState('idle');
      setError(message);
      showToast(message, 'error');
    }
  }

  return (
    <section className="page-shell pm-console" aria-labelledby="pm-console-title">
      <header className="page-header">
        <div>
          <p className="eyebrow">PM Agent</p>
          <h1 id="pm-console-title">Feedback to PRD pipeline</h1>
          <p className="page-lead">
            Capture raw feedback, cluster insights, form an opportunity, draft a PRD, and optionally run the existing review quality gate.
          </p>
        </div>
      </header>

      <div className="workspace-grid pm-console-grid">
        <PanelErrorBoundary title="Feedback input">
          <form className="panel" onSubmit={handleRunPipeline}>
            <div className="panel-header">
              <h2>Feedback inbox</h2>
              <p>{feedbackCount} items ready</p>
            </div>
            <label className="field">
              <span>Product hint</span>
              <input
                value={productHint}
                onChange={(event) => setProductHint(event.target.value)}
                placeholder="e.g. commerce"
              />
            </label>
            <label className="field">
              <span>Feedback lines</span>
              <textarea
                rows={10}
                value={feedbackText}
                onChange={(event) => setFeedbackText(event.target.value)}
                placeholder="One feedback item per line"
              />
            </label>
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={runQualityGate}
                onChange={(event) => setRunQualityGate(event.target.checked)}
              />
              <span>Run review quality gate after PRD draft</span>
            </label>
            {error ? <p className="form-error" role="alert">{error}</p> : null}
            <div className="action-row">
              <button type="submit" className="primary-button" disabled={submitState === 'running'}>
                {submitState === 'running' ? 'Running pipeline…' : 'Run PM pipeline'}
              </button>
              <button
                type="button"
                className="ghost-button"
                onClick={() => setFeedbackText(sampleFeedback)}
              >
                Load sample
              </button>
            </div>
          </form>
        </PanelErrorBoundary>

        <PanelErrorBoundary title="Pipeline status">
          <div className="panel">
            <div className="panel-header">
              <h2>Current task</h2>
              <p>{result?.status || 'idle'}</p>
            </div>
            {result ? (
              <dl className="meta-list">
                <div>
                  <dt>Pipeline</dt>
                  <dd>{result.pipeline_id}</dd>
                </div>
                <div>
                  <dt>Stage</dt>
                  <dd>{result.stage}</dd>
                </div>
                <div>
                  <dt>Opportunity</dt>
                  <dd>{result.opportunity_id || '—'}</dd>
                </div>
                <div>
                  <dt>PRD</dt>
                  <dd>{result.prd_id || '—'}</dd>
                </div>
                <div>
                  <dt>Review run</dt>
                  <dd>
                    {result.review_run_id ? (
                      <Link to={`/run/${result.review_run_id}`}>{result.review_run_id}</Link>
                    ) : (
                      '—'
                    )}
                  </dd>
                </div>
              </dl>
            ) : (
              <p className="empty-copy">Run the pipeline to see progress and next actions.</p>
            )}
          </div>
        </PanelErrorBoundary>
      </div>

      {result ? (
        <div className="workspace-grid pm-console-results">
          <PanelErrorBoundary title="Insights">
            <div className="panel">
              <div className="panel-header">
                <h2>Insights</h2>
                <p>{(result.insights || []).length} clusters</p>
              </div>
              <ul className="stack-list">
                {(result.insights || []).map((insight) => (
                  <li key={insight.id}>
                    <strong>{insight.title}</strong>
                    <p>{insight.summary || insight.theme || 'No summary'}</p>
                    <small>{(insight.source_refs || []).join(', ')}</small>
                  </li>
                ))}
              </ul>
            </div>
          </PanelErrorBoundary>

          <PanelErrorBoundary title="Opportunity">
            <div className="panel">
              <div className="panel-header">
                <h2>Opportunity</h2>
                <p>{result.opportunity?.title || '—'}</p>
              </div>
              {result.opportunity ? (
                <dl className="meta-list">
                  <div>
                    <dt>Problem</dt>
                    <dd>{result.opportunity.problem || '—'}</dd>
                  </div>
                  <div>
                    <dt>Users</dt>
                    <dd>{result.opportunity.users || '—'}</dd>
                  </div>
                  <div>
                    <dt>Value</dt>
                    <dd>{result.opportunity.value || '—'}</dd>
                  </div>
                  <div>
                    <dt>Open questions</dt>
                    <dd>{(result.opportunity.open_questions || []).join('; ') || '—'}</dd>
                  </div>
                </dl>
              ) : (
                <p className="empty-copy">No opportunity yet.</p>
              )}
            </div>
          </PanelErrorBoundary>

          <PanelErrorBoundary title="PRD draft">
            <div className="panel">
              <div className="panel-header">
                <h2>PRD draft</h2>
                <p>{result.prd?.title || '—'}</p>
              </div>
              {result.prd ? (
                <pre className="code-block">{result.prd.markdown}</pre>
              ) : (
                <p className="empty-copy">No PRD draft yet.</p>
              )}
            </div>
          </PanelErrorBoundary>

          <PanelErrorBoundary title="Evidence chain">
            <div className="panel">
              <div className="panel-header">
                <h2>Evidence chain</h2>
                <p>Feedback → Insight → Opportunity → PRD</p>
              </div>
              <ol className="stack-list">
                <li>
                  Feedback IDs: {(result.feedback_ids || []).join(', ') || '—'}
                </li>
                <li>
                  Insight IDs: {(result.insight_ids || []).join(', ') || '—'}
                </li>
                <li>
                  Opportunity: {result.opportunity_id || '—'}
                </li>
                <li>
                  PRD: {result.prd_id || '—'}
                  {result.prd?.evidence_refs?.length ? (
                    <small>
                      {' '}
                      refs: {result.prd.evidence_refs.join(', ')}
                    </small>
                  ) : null}
                </li>
              </ol>
            </div>
          </PanelErrorBoundary>
        </div>
      ) : null}
    </section>
  );
}

export default PmConsolePage;
