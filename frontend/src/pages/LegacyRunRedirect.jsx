import { useEffect, useState } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import { lookupProjectByRun } from '../api';

export default function LegacyRunRedirect() {
  const { runId = '' } = useParams();
  const [projectId, setProjectId] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    lookupProjectByRun(runId)
      .then((payload) => {
        if (!cancelled) {
          setProjectId(payload.project_id);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message || 'Unable to resolve project for this run.');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (projectId) {
    return <Navigate to={`/projects/${projectId}/reviews/${runId}`} replace />;
  }

  if (error) {
    return (
      <main className="panel stack">
        <h1>Run not linked to a project</h1>
        <p>{error}</p>
        <Link to="/">Back to projects</Link>
      </main>
    );
  }

  return <main className="panel">Redirecting to project review…</main>;
}
