import { Link, NavLink, useLocation } from 'react-router-dom';

function SunIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="4.2" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <path
        d="M12 2.75v2.5M12 18.75v2.5M21.25 12h-2.5M5.25 12h-2.5M18.54 5.46l-1.77 1.77M7.23 16.77l-1.77 1.77M18.54 18.54l-1.77-1.77M7.23 7.23 5.46 5.46"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeWidth="1.8"
      />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M16.62 3.91a7.95 7.95 0 1 0 3.47 12.18 8.82 8.82 0 0 1-11.52-11.7 8.72 8.72 0 0 0 8.05-.48Z"
        fill="none"
        stroke="currentColor"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </svg>
  );
}

function Navbar({ theme, onToggleTheme }) {
  const location = useLocation();
  const isHistoryActive = location.pathname === '/' && location.hash === '#history';
  const isCompareActive = location.pathname === '/compare';
  const isTrendsActive = location.pathname === '/trends';
  const isPmActive = location.pathname === '/pm';
  const isDark = theme === 'dark';

  return (
    <header className="navbar-shell">
      <div className="navbar">
        <NavLink to="/" end className="navbar-brand" aria-label="Review Workspace home">
          <span className="navbar-logo" aria-hidden="true">RW</span>
          <span className="navbar-brand-copy">
            <strong>Review Workspace</strong>
            <small>Requirement review cockpit</small>
          </span>
        </NavLink>

        <div className="navbar-actions">
          <nav className="navbar-links" aria-label="Primary">
            <NavLink
              to="/"
              end
              className={({ isActive }) => `nav-link${isActive ? ' nav-link-active' : ''}`}
              aria-label="Go to home page"
            >
              Home
            </NavLink>
            <Link
              to={{ pathname: '/', hash: '#history' }}
              className={`nav-link${isHistoryActive ? ' nav-link-active' : ''}`}
              aria-label="Jump to review history"
            >
              History
            </Link>
            <NavLink
              to="/compare"
              className={`nav-link${isCompareActive ? ' nav-link-active' : ''}`}
              aria-label="Open run comparison"
            >
              对比
            </NavLink>
            <NavLink
              to="/trends"
              className={`nav-link${isTrendsActive ? ' nav-link-active' : ''}`}
              aria-label="Open trend analysis"
            >
              趋势
            </NavLink>
            <NavLink
              to="/pm"
              className={`nav-link${isPmActive ? ' nav-link-active' : ''}`}
              aria-label="Open PM Agent console"
            >
              PM Agent
            </NavLink>
            <NavLink
              to="/feishu"
              className={({ isActive }) => `nav-link${isActive ? ' nav-link-active' : ''}`}
              aria-label="Open Feishu entry page"
            >
              Feishu Entry
            </NavLink>
          </nav>

          <button
            type="button"
            className="theme-toggle"
            onClick={onToggleTheme}
            aria-label={`Switch to ${isDark ? 'light' : 'dark'} mode`}
            title={`Switch to ${isDark ? 'light' : 'dark'} mode`}
          >
            <span className="theme-toggle-icon">{isDark ? <SunIcon /> : <MoonIcon />}</span>
            <span className="theme-toggle-label">{isDark ? 'Light mode' : 'Dark mode'}</span>
          </button>

          <div className="navbar-avatar-slot" aria-label="Reserved user profile space" role="img">
            <span>U</span>
          </div>
        </div>
      </div>
    </header>
  );
}

export default Navbar;
