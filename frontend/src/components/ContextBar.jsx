import { IconChevronRight } from '@tabler/icons-react';
import { useLocation } from 'react-router-dom';
const labels = { '/agent': '任务', '/projects': '项目', '/workbench': '决策工作台', '/settings/providers': '模型连接' };
export default function ContextBar() {
 const { pathname } = useLocation();
 const key = Object.keys(labels).find((item) => pathname === item || pathname.startsWith(`${item}/`));
 return <header className="context-bar"><span>{labels[key] || 'PRD Pal'}</span>{pathname.startsWith('/agent/') ? <><IconChevronRight size={15}/><span className="context-muted">当前会话</span></> : null}</header>;
}
