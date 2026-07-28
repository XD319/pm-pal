import { useEffect } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import ArtifactDownloadPanel from '../components/ArtifactDownloadPanel';
import ClarificationPanel from '../components/ClarificationPanel';
import ConflictResolutionPanel from '../components/ConflictResolutionPanel';
import FindingsPanel from '../components/FindingsPanel';
import PanelErrorBoundary from '../components/PanelErrorBoundary';
import ReviewerInsightsPanel from '../components/ReviewerInsightsPanel';
import RevisionDecisionPanel from '../components/RevisionDecisionPanel';
import ReviewSummaryPanel from '../components/ReviewSummaryPanel';
import RisksPanel from '../components/RisksPanel';
import RunProgressCard from '../components/RunProgressCard';
import ToolTracePanel from '../components/ToolTracePanel';
import useReviewRun from '../hooks/useReviewRun';
import useReviewRunSSE from '../hooks/useReviewRunSSE';

function RunDetailsPage() {
  const { projectId = '', runId = '' } = useParams();
  const [searchParams] = useSearchParams();
  const isFeishuEmbed = searchParams.get('embed') === 'feishu';
  const projectHome = projectId ? `/projects/${projectId}` : '/';
  const sseRun = useReviewRunSSE(runId, { fallbackToPolling: true });
  const {
    runState,
    status,
    result,
    refreshStatus,
    downloadArtifact,
    submitClarification,
    submitRevisionDecision,
    submitRevisionInput,
    submitRevisionConfirmAction,
    generateRoadmap,
  } = useReviewRun(runId, {
    externalStatusPayload: sseRun.statusPayload,
    externalLoadError: sseRun.error,
    disableStatusPolling: true,
  });
  const revisionStage =
    runState.resultPayload?.revision_stage
    ?? sseRun.statusPayload?.revision_stage
    ?? runState.statusPayload?.revision_stage
    ?? {};
  const showRevisionSection = Boolean(
    runState.resultState === 'ready'
    && result
    && revisionStage.available,
  );

  useEffect(() => () => {
    sseRun.closeConnection();
  }, [sseRun.closeConnection]);

  return (
    <>
      <header className={`page-header${isFeishuEmbed ? ' page-header-embed' : ''}`}>
        {!isFeishuEmbed ? (
          <nav className="breadcrumbs" aria-label="Breadcrumb">
            <Link to="/" aria-label="Return to projects">Projects</Link>
            <span>{'>'}</span>
            {projectId ? (
              <>
                <Link to={projectHome}>{projectId}</Link>
                <span>{'>'}</span>
                <span>Review</span>
                <span>{'>'}</span>
              </>
            ) : (
              <>
                <span>Run</span>
                <span>{'>'}</span>
              </>
            )}
            <span aria-current="page">{runId}</span>
          </nav>
        ) : null}

        <div className={`page-header-row${isFeishuEmbed ? ' page-header-row-embed' : ''}`}>
          <div>
            <p className="eyebrow">{isFeishuEmbed ? 'Feishu review run' : 'Project review workspace'}</p>
            <h1>Run {runId}</h1>
            <p className="hero-copy">
              {isFeishuEmbed
                ? 'Track progress, review findings, respond to clarification prompts, decide whether to revise the PRD, and open artifacts from a compact mobile-friendly detail page.'
                : 'Track execution, inspect structured review output, decide whether to revise the PRD, and download artifacts from the project-scoped review URL.'}
            </p>
          </div>

          <div className="action-row">
            {!isFeishuEmbed ? (
              <Link to={projectHome} className="ghost-button" aria-label="Return to project">
                Back to project
              </Link>
            ) : null}
            <button
              type="button"
              className="ghost-button"
              onClick={() => refreshStatus()}
              aria-label={`Refresh status for run ${runId}`}
            >
              Refresh status
            </button>
          </div>
        </div>

        {runState.loadError && <div className="feedback-banner feedback-error" aria-live="polite">{runState.loadError}</div>}
      </header>

      <main className={`workspace-grid workspace-grid-detail${isFeishuEmbed ? ' workspace-grid-detail-embed' : ''}`}>
        <section className="stack">
          <PanelErrorBoundary panelTitle="Run Progress" resetKey={`${runId}:${status}`}>
            <RunProgressCard
              runId={runId}
              status={status}
              statusPayload={sseRun.statusPayload ?? runState.statusPayload}
              failureMessage={runState.failureMessage || sseRun.error}
              loadState={sseRun.loadState === 'loading' ? sseRun.loadState : runState.loadState}
              isConnected={sseRun.isConnected}
              isPolling={sseRun.isPolling}
            />
          </PanelErrorBoundary>
        </section>

        <section className="stack stack-wide">
          <PanelErrorBoundary panelTitle="Result Overview" resetKey={`${runId}:${runState.resultState}`}>
            <ReviewSummaryPanel
              runId={runId}
              status={status}
              result={result}
              statusPayload={sseRun.statusPayload ?? runState.statusPayload}
              resultPayload={runState.resultPayload}
              resultState={runState.resultState}
              failureMessage={runState.failureMessage}
              resultError={runState.resultError}
            />
          </PanelErrorBoundary>

          {!isFeishuEmbed ? (
            <div className="panel-grid panel-grid-two-up">
              <PanelErrorBoundary panelTitle="Reviewer Insights" resetKey={`${runId}:${runState.resultState}:reviewer-insights`}>
                <ReviewerInsightsPanel result={result} resultPayload={runState.resultPayload} />
              </PanelErrorBoundary>
              <PanelErrorBoundary panelTitle="Tool Trace" resetKey={`${runId}:${runState.resultState}:tool-trace`}>
                <ToolTracePanel result={result} />
              </PanelErrorBoundary>
            </div>
          ) : null}

          {!isFeishuEmbed ? (
            <PanelErrorBoundary panelTitle="Conflict Resolution" resetKey={`${runId}:${runState.resultState}:conflicts`}>
              <ConflictResolutionPanel result={result} />
            </PanelErrorBoundary>
          ) : null}

          <div className="panel-grid panel-grid-two-up">
            <PanelErrorBoundary panelTitle="Findings" resetKey={`${runId}:${runState.resultState}:findings`}>
              <FindingsPanel result={result} status={status} resultState={runState.resultState} />
            </PanelErrorBoundary>
            <PanelErrorBoundary panelTitle="Risks" resetKey={`${runId}:${runState.resultState}:risks`}>
              <RisksPanel result={result} />
            </PanelErrorBoundary>
          </div>

          <section id="clarification" aria-label="澄清回答区">
            <PanelErrorBoundary panelTitle="Clarification Panel" resetKey={`${runId}:${runState.resultState}:questions`}>
              <ClarificationPanel
                result={result}
                onSubmit={submitClarification}
                isSubmitting={runState.clarificationState === 'submitting'}
              />
            </PanelErrorBoundary>
          </section>

          {showRevisionSection ? (
            <section id="revise-prd" aria-label="修订 PRD 决策区">
              <PanelErrorBoundary panelTitle="Revision Decision" resetKey={`${runId}:${runState.resultState}:revision-stage`}>
                <RevisionDecisionPanel
                  runId={runId}
                  revisionStage={revisionStage}
                  onDecide={submitRevisionDecision}
                  onSubmitRevisionInput={submitRevisionInput}
                  onConfirmAction={submitRevisionConfirmAction}
                  isSubmitting={runState.revisionDecisionState === 'submitting'}
                  isSubmittingInput={runState.revisionInputState === 'submitting'}
                  isSubmittingConfirm={runState.revisionConfirmState === 'submitting'}
                />
              </PanelErrorBoundary>
            </section>
          ) : null}

          <section id="next-delivery" aria-label="下一步交付区">
            <PanelErrorBoundary panelTitle="Artifacts" resetKey={`${runId}:${runState.downloadFormat}`}>
              <ArtifactDownloadPanel
                runId={runId}
                status={status}
                resultPayload={runState.resultPayload}
                statusPayload={sseRun.statusPayload ?? runState.statusPayload}
                revisionStage={revisionStage}
                downloadFormat={runState.downloadFormat}
                onDownload={downloadArtifact}
                onGenerateRoadmap={generateRoadmap}
                isGeneratingRoadmap={runState.roadmapGenerationState === 'submitting'}
              />
            </PanelErrorBoundary>
          </section>
        </section>
      </main>
    </>
  );
}

export default RunDetailsPage;
