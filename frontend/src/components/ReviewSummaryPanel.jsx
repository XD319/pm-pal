import '../styles/panels.css';
import '../styles/components.css';
import { deriveGatingInfo, deriveMemoryHits, deriveModeLabel, deriveReviewers, deriveSummary } from '../utils/derivers';

function ReviewSummaryPanel({ runId, status, result, statusPayload, resultPayload, resultState, failureMessage, resultError }) {
  const summary = deriveSummary(result, runId, statusPayload, resultPayload);
  const reviewers = deriveReviewers(result, resultPayload);
  const gating = deriveGatingInfo(result, resultPayload);
  const memoryHits = result ? deriveMemoryHits(result, resultPayload) : [];
  const similarReferences = resultPayload?.similar_reviews_referenced ?? result?.similar_reviews_referenced ?? [];

  return (
    <section className="panel review-summary-panel">
      <div className="panel-header">
        <div>
          <p className="section-kicker">Review Results</p>
          <h2>Result overview</h2>
        </div>
        {result && <span className="inline-meta" aria-live="polite">{deriveModeLabel(result)}</span>}
      </div>

      {status === 'failed' ? (
        <div className="empty-state empty-state-soft">
          <div className="empty-grid" />
          <h3>Run failed before review output was ready</h3>
          <p>{failureMessage}</p>
        </div>
      ) : resultState === 'loading' ? (
        <div className="loading-state loading-state-summary">
          <div className="shimmer-block shimmer-title" />
          <div className="metric-grid">
            <div className="metric-card loading-card" />
            <div className="metric-card loading-card" />
            <div className="metric-card loading-card" />
            <div className="metric-card loading-card" />
          </div>
        </div>
      ) : result ? (
        <div className="result-content">
          <div className="result-lead">
            <h3>{summary.title}</h3>
            <p>{summary.narrative}</p>
          </div>

          <div className="metric-grid">
            {summary.metrics.map((metric) => (
              <div key={metric.label} className="metric-card">
                <span>{metric.label}</span>
                <strong>{metric.value}</strong>
              </div>
            ))}
          </div>

          <div className="chip-row">
            {summary.chips.map((chip) => (
              <span key={chip} className="inline-meta inline-meta-soft">{chip}</span>
            ))}
          </div>

          {(gating.skipped || result?.partial_review || result?.manual_review_required || result?.parallel_review?.manual_review_required) && (
            <div className="feedback-banner feedback-error" aria-live="polite">
              {result?.manual_review_message || result?.parallel_review?.manual_review_message || 'This run needs partial or manual review follow-up.'}
            </div>
          )}

          <div className="panel-grid panel-grid-two-up">
            <div className="metric-card">
              <span>Selected reviewers</span>
              <strong>{reviewers.used.length > 0 ? reviewers.used.join(', ') : 'None'}</strong>
            </div>
            <div className="metric-card">
              <span>Skipped reviewers</span>
              <strong>
                {reviewers.skipped.length > 0
                  ? reviewers.skipped.map((item) => item.reviewer ?? item).join(', ')
                  : 'None'}
              </strong>
            </div>
          </div>

          <div className="panel-grid panel-grid-two-up">
            <div className="metric-card">
              <span>Memory references</span>
              <strong>{memoryHits.length}</strong>
            </div>
            <div className="metric-card">
              <span>Referenced IDs</span>
              <strong>{similarReferences.length > 0 ? similarReferences.join(', ') : 'None'}</strong>
            </div>
          </div>

          {memoryHits.length > 0 && (
            <details className="metric-card">
              <summary>View memory references</summary>
              <div className="list-stack" style={{ marginTop: '1rem' }}>
                {memoryHits.map((hit) => (
                  <article key={hit.id} className="finding-card insight-card">
                    <div className="finding-header">
                      <h4>{hit.title}</h4>
                      <span className="inline-meta inline-meta-soft">{hit.sourceKind}</span>
                    </div>
                    {hit.summary && <p>{hit.summary}</p>}
                    {hit.findingExcerpt && <div className="subtle-note">{hit.findingExcerpt}</div>}
                    <div className="detail-row">
                      <span>Score: {hit.score.toFixed(2)}</span>
                      <span>Mode: {hit.reviewMode}</span>
                    </div>
                  </article>
                ))}
              </div>
            </details>
          )}

          <div className="result-lead">
            <h3>Gating info</h3>
            <p>Mode decision: {deriveModeLabel({ mode: gating.selectedMode })}</p>
            <div className="chip-row">
              {gating.reasons.map((reason) => (
                <span key={reason} className="inline-meta inline-meta-soft">{reason}</span>
              ))}
            </div>
          </div>
        </div>
      ) : (
        <div className="empty-state empty-state-soft">
          <div className="empty-grid" />
          <h3>Review summary will land here</h3>
          <p>
            The workspace will pull structured output from <code>GET /api/projects/{'{project_id}'}/reviews/{'{run_id}'}/result</code> as soon as the run completes.
          </p>
          {resultError && <div className="feedback-banner feedback-error" aria-live="polite">{resultError}</div>}
        </div>
      )}
    </section>
  );
}

export default ReviewSummaryPanel;
