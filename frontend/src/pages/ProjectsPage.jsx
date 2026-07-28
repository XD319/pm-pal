import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { createProject, listProjects } from '../api';

export default function ProjectsPage() {
  const [projects, setProjects] = useState([]); const [name, setName] = useState(''); const [description, setDescription] = useState(''); const [error, setError] = useState(''); const navigate = useNavigate();
  async function load() { try { setProjects((await listProjects()).projects || []); } catch (e) { setError(e.message); } }
  useEffect(() => { load(); }, []);
  async function submit(e) { e.preventDefault(); try { const project = await createProject({ name, description }); navigate(`/projects/${project.id}`); } catch (err) { setError(err.message); } }
  return <main className="stack project-space-page"><header className="hero hero-tight"><div><p className="eyebrow">PRD Pal</p><h1>你的 AI 项目空间</h1><p className="hero-copy">将 PRD、飞书文档和评审决策放在同一个长期上下文中。</p></div><Link className="secondary-button" to="/settings/providers">模型连接</Link></header><section className="workspace-grid"><form className="panel stack" onSubmit={submit}><div><p className="section-kicker">新建项目</p><h2>开始一个项目</h2></div><label className="field"><span>项目名称</span><input value={name} onChange={e => setName(e.target.value)} required placeholder="例如：移动端会员改版" /></label><label className="field"><span>项目说明</span><textarea value={description} onChange={e => setDescription(e.target.value)} placeholder="目标、背景或团队约定" /></label><button className="primary-button">创建项目</button>{error ? <p className="form-error">{error}</p> : null}</form><section className="panel"><div className="panel-header"><div><p className="section-kicker">最近项目</p><h2>继续工作</h2></div><button className="ghost-button" onClick={load}>刷新</button></div><ul className="stack-list">{projects.map(p => <li key={p.id}><Link to={`/projects/${p.id}`}><strong>{p.name}</strong></Link><p>{p.description || '暂无项目说明'}</p><small>{p.source_count} 份资料 · {p.run_count} 次评审</small></li>)}{!projects.length ? <li className="empty-copy">还没有项目。创建后添加 PRD、会议纪要或飞书链接。</li> : null}</ul></section></section></main>;
}
