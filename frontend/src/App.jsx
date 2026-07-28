import { lazy, Suspense, useEffect } from 'react';
import { Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom';
import Navbar from './components/Navbar';
import RouteLoadingFallback from './components/RouteLoadingFallback';
import { useTheme } from './hooks/useTheme';
import ProjectsPage from './pages/ProjectsPage';
import ProjectDetailPage from './pages/ProjectDetailPage';
import ProviderSettingsPage from './pages/ProviderSettingsPage';
import './styles/layout.css';

const RunDetailsPage = lazy(() => import('./pages/RunDetailsPage'));
const LegacyRunRedirect = lazy(() => import('./pages/LegacyRunRedirect'));

function AppLayout() {
  const location = useLocation();
  const { theme, toggleTheme } = useTheme();
  const searchParams = new URLSearchParams(location.search);
  const isFeishuEmbed = (
    (location.pathname.startsWith('/run/') || /\/projects\/[^/]+\/reviews\//.test(location.pathname))
    && searchParams.get('embed') === 'feishu'
  );

  useEffect(() => {
    if (location.hash) {
      const targetId = location.hash.slice(1);
      window.requestAnimationFrame(() => {
        document.getElementById(targetId)?.scrollIntoView({
          behavior: 'smooth',
          block: 'start',
        });
      });
      return;
    }

    window.scrollTo({
      top: 0,
      left: 0,
      behavior: 'auto',
    });
  }, [location.pathname, location.hash]);

  return (
    <div className={`app-shell${isFeishuEmbed ? ' app-shell-embed' : ''}`}>
      <div className="ambient ambient-left" />
      <div className="ambient ambient-right" />
      {!isFeishuEmbed ? <Navbar theme={theme} onToggleTheme={toggleTheme} /> : null}

      <div className={`page-shell${isFeishuEmbed ? ' page-shell-embed' : ''}`}>
        <Outlet />
      </div>
    </div>
  );
}

function App() {
  return (
    <Routes>
      <Route path="/" element={<AppLayout />}>
        <Route index element={<ProjectsPage />} />
        <Route path="projects" element={<ProjectsPage />} />
        <Route path="projects/:projectId" element={<ProjectDetailPage />} />
        <Route path="settings/providers" element={<ProviderSettingsPage />} />
        <Route
          path="projects/:projectId/reviews/:runId"
          element={(
            <Suspense fallback={<RouteLoadingFallback />}>
              <RunDetailsPage />
            </Suspense>
          )}
        />
        <Route
          path="run/:runId"
          element={(
            <Suspense fallback={<RouteLoadingFallback />}>
              <LegacyRunRedirect />
            </Suspense>
          )}
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

export default App;

