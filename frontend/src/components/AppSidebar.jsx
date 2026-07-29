import { useState } from 'react';
import { NavLink, useLocation, useNavigate } from 'react-router-dom';
import { IconPlus, IconFolder, IconScale, IconPlugConnected, IconMoon, IconSun, IconMenu2, IconX, IconMessageCircle } from '@tabler/icons-react';

const navItems = [
  { to: '/projects', label: '项目', icon: IconFolder },
  { to: '/workbench', label: '决策工作台', icon: IconScale },
  { to: '/settings/providers', label: '模型连接', icon: IconPlugConnected },
];

export default function AppSidebar({ theme, onToggleTheme }) {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const close = () => setOpen(false);
  const newTask = () => { close(); navigate('/agent'); };
  return <>
    <button className="sidebar-menu" onClick={() => setOpen(true)} aria-label="打开导航"><IconMenu2 size={20} /></button>
    {open ? <button className="sidebar-backdrop" onClick={close} aria-label="关闭导航" /> : null}
    <aside className={`app-sidebar ${open ? 'app-sidebar-open' : ''}`}>
      <div className="sidebar-top">
        <NavLink to="/agent" className="sidebar-brand" onClick={close}><span>PP</span><strong>PRD Pal</strong></NavLink>
        <button className="sidebar-close" onClick={close} aria-label="关闭导航"><IconX size={18} /></button>
      </div>
      <button className="sidebar-new-task" onClick={newTask}><IconPlus size={17} stroke={1.8} />新建任务</button>
      <nav className="sidebar-nav" aria-label="全局导航">
        <NavLink to="/agent" className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''}`} onClick={close}><IconMessageCircle size={18} stroke={1.7} />任务</NavLink>
        {navItems.map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''}`} onClick={close}><Icon size={18} stroke={1.7} />{label}</NavLink>)}
      </nav>
      <div className="sidebar-bottom">
        <button className="sidebar-theme" onClick={onToggleTheme}>{theme === 'dark' ? <IconSun size={17} /> : <IconMoon size={17} />}{theme === 'dark' ? '浅色模式' : '深色模式'}</button>
      </div>
    </aside>
  </>;
}
