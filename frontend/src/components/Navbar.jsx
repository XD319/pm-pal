import { NavLink } from 'react-router-dom';

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
  const isDark = theme === 'dark';

  return (
    <header className="navbar-shell">
      <div className="navbar">
        <NavLink to="/" end className="navbar-brand" aria-label="PM Pal 产品工作台">
          <span className="navbar-logo" aria-hidden="true">PP</span>
          <span className="navbar-brand-copy">
            <strong>PM Pal</strong>
            <small>产品协作工作台</small>
          </span>
        </NavLink>

        <div className="navbar-actions">
          <nav className="navbar-links" aria-label="主导航">
            <NavLink to="/agent" className={({ isActive }) => `nav-link${isActive ? ' nav-link-active' : ''}`}>工作助手</NavLink>
            <NavLink to="/projects" className={({ isActive }) => `nav-link${isActive ? ' nav-link-active' : ''}`}>项目</NavLink>
            <NavLink to="/workbench" className={({ isActive }) => `nav-link${isActive ? ' nav-link-active' : ''}`}>决策工作台</NavLink>
            <NavLink to="/settings/providers" className={({ isActive }) => `nav-link${isActive ? ' nav-link-active' : ''}`}>模型连接</NavLink>
          </nav>

          <button
            type="button"
            className="theme-toggle"
            onClick={onToggleTheme}
            aria-label="切换浅色或深色模式"
            title="切换浅色或深色模式"
          >
            <span className="theme-toggle-icon">{isDark ? <SunIcon /> : <MoonIcon />}</span>
            <span className="theme-toggle-label">{isDark ? '浅色模式' : '深色模式'}</span>
          </button>
        </div>
      </div>
    </header>
  );
}

export default Navbar;

