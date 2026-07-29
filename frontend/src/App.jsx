import { lazy, Suspense, useEffect } from 'react';
import { Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom';
import AppSidebar from './components/AppSidebar';
import ContextBar from './components/ContextBar';
import RouteLoadingFallback from './components/RouteLoadingFallback';
import { useTheme } from './hooks/useTheme';
import ProjectsPage from './pages/ProjectsPage';
import ProjectDetailPage from './pages/ProjectDetailPage';
import AgentWorkspacePage from './pages/AgentWorkspacePage';
import ProviderSettingsPage from './pages/ProviderSettingsPage';
import './styles/agent.css';
import './styles/agent-workspace.css';
import './styles/layout.css';
import './styles/workspace.css';
import './styles/codex-shell.css';
const RunDetailsPage = lazy(() => import('./pages/RunDetailsPage'));
const LegacyRunRedirect = lazy(() => import('./pages/LegacyRunRedirect'));
const PmConsolePage = lazy(() => import('./pages/PmConsolePage'));
function AppLayout() {
 const location = useLocation(); const { theme, toggleTheme } = useTheme();
 const isEmbed = (location.pathname.startsWith('/run/') || /\/projects\/[^/]+\/reviews\//.test(location.pathname)) && new URLSearchParams(location.search).get('embed') === 'feishu';
 useEffect(() => { window.scrollTo({ top: 0, left: 0, behavior: 'auto' }); }, [location.pathname]);
 if (isEmbed) return <div className="app-shell app-shell-embed"><div className="page-shell page-shell-embed"><Outlet /></div></div>;
 return <div className="codex-app"><AppSidebar theme={theme} onToggleTheme={toggleTheme}/><div className="codex-main"><ContextBar/><div className="page-shell"><Outlet/></div></div></div>;
}
export default function App() { return <Routes><Route path="/" element={<AppLayout/>}><Route index element={<Navigate to="/agent" replace/>}/><Route path="agent" element={<AgentWorkspacePage/>}/><Route path="agent/:conversationId" element={<AgentWorkspacePage/>}/><Route path="projects" element={<ProjectsPage/>}/><Route path="projects/:projectId" element={<ProjectDetailPage/>}/><Route path="settings/providers" element={<ProviderSettingsPage/>}/><Route path="workbench" element={<Suspense fallback={<RouteLoadingFallback/>}><PmConsolePage/></Suspense>}/><Route path="projects/:projectId/reviews/:runId" element={<Suspense fallback={<RouteLoadingFallback/>}><RunDetailsPage/></Suspense>}/><Route path="run/:runId" element={<Suspense fallback={<RouteLoadingFallback/>}><LegacyRunRedirect/></Suspense>}/><Route path="*" element={<Navigate to="/agent" replace/>}/></Route></Routes>; }
