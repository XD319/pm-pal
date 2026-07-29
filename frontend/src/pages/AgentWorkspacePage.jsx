import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { IconArrowUp, IconFileText, IconRefresh, IconExternalLink, IconLoader2, IconPlus } from '@tabler/icons-react';
import { formatStatus } from '../utils/presentation';

const prompts = ['整理这份 PRD，找出需求遗漏并列出待确认问题。', '汇总当前产品的用户反馈，并生成机会草案。', '请对当前 PRD 发起评审。'];
async function request(path, options = {}) { const response = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...options }); const payload = await response.json(); if (!response.ok) throw new Error(payload?.detail?.message || '请求失败，请稍后重试。'); return payload; }
function timeLabel(value) { if (!value) return ''; const date = new Date(value); return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' }); }
export default function AgentWorkspacePage() {
 const { conversationId } = useParams(); const [params] = useSearchParams(); const navigate = useNavigate();
 const [conversation, setConversation] = useState(null); const [history, setHistory] = useState([]); const [content, setContent] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
 const refreshHistory = async () => { const result = await request('/api/agent/conversations'); setHistory(result.conversations || []); };
 const load = async (id) => { const result = await request(`/api/agent/conversations/${id}`); setConversation(result); };
 useEffect(() => { refreshHistory().catch((err) => setError(err.message)); }, []);
 useEffect(() => { if (conversationId) load(conversationId).catch((err) => setError(err.message)); else setConversation(null); }, [conversationId]);
 async function ensureConversation() { if (conversation) return conversation; const result = await request('/api/agent/conversations', { method: 'POST', body: JSON.stringify({ product_id: params.get('product_id') || '' }) }); await refreshHistory(); navigate(`/agent/${result.conversation.id}`, { replace: true }); setConversation(result); return result; }
 async function submit(event) { event?.preventDefault(); if (!content.trim()) return; setBusy(true); setError(''); try { const current = await ensureConversation(); const next = await request(`/api/agent/conversations/${current.conversation.id}/messages`, { method: 'POST', body: JSON.stringify({ content }) }); setConversation(next); setContent(''); await refreshHistory(); } catch (err) { setError(err.message); } finally { setBusy(false); } }
 async function confirm(taskId, confirmed) { setBusy(true); setError(''); try { await request(`/api/agent/tasks/${taskId}/confirm`, { method: 'POST', body: JSON.stringify({ confirmed, actor: params.get('open_id') || 'local' }) }); if (conversation?.conversation.id) await load(conversation.conversation.id); await refreshHistory(); } catch (err) { setError(err.message); } finally { setBusy(false); } }
 const tasks = useMemo(() => Object.fromEntries((conversation?.tasks || []).map((task) => [task.id, task])), [conversation]);
 const messages = conversation?.messages || [];
 return <main className="agent-codex-page">
   <aside className="task-history"><div className="history-heading"><span>最近任务</span><button onClick={() => navigate('/agent')} aria-label="新建任务"><IconPlus size={17}/></button></div>{history.length ? <div className="history-list">{history.map((item) => <button key={item.id} onClick={() => navigate(`/agent/${item.id}`)} className={`history-item ${item.id === conversationId ? 'selected' : ''}`}><strong>{item.title}</strong><span>{formatStatus(item.latest_task_status)} · {timeLabel(item.updated_at)}</span></button>)}</div> : <p className="history-empty">尚无任务历史</p>}</aside>
   <section className="conversation-canvas">
     <div className="conversation-scroll">
       {!conversation ? <div className="conversation-empty"><IconFileText size={30} stroke={1.5}/><h1>从一个任务开始</h1><p>描述你要推进的产品工作。PRD Pal 会保留来源、计划和需要确认的关键节点。</p><div className="prompt-grid">{prompts.map((prompt) => <button key={prompt} onClick={() => setContent(prompt)}>{prompt}</button>)}</div></div> : <>
         <div className="conversation-title"><h1>{conversation.conversation.title || '未命名任务'}</h1><span>{conversation.conversation.project_id ? '已关联项目' : '独立会话'}</span></div>
         <div className="message-list" aria-live="polite">{messages.map((message) => { const task = message.payload?.task_id ? tasks[message.payload.task_id] : null; return <article key={message.id} className={`message-row ${message.role === 'user' ? 'message-user' : 'message-assistant'}`}><span className="message-role">{message.role === 'user' ? '你' : 'PRD Pal'}</span><div className="message-content"><p>{message.content}</p>{message.payload?.references?.map((ref) => <a key={ref.url} href={ref.url} target="_blank" rel="noreferrer"><IconExternalLink size={14}/>查看来源</a>)}{task ? <div className={`task-action task-${task.status}`}><div><strong>{task.title}</strong><span>{formatStatus(task.status)}</span></div><p>{task.details?.next_step}</p>{task.status === 'awaiting_confirmation' ? <div><button className="primary-button" disabled={busy} onClick={() => confirm(task.id, true)}>确认执行</button><button className="inline-button" disabled={busy} onClick={() => confirm(task.id, false)}>暂不处理</button></div> : null}{task.status === 'failed' ? <div><button className="primary-button" disabled={busy} onClick={() => confirm(task.id, true)}><IconRefresh size={15}/>重新尝试</button><Link className="inline-button" to="/settings/providers">检查模型连接</Link></div> : null}{task.details?.result?.run_id ? <Link className="inline-button" to={`/projects/${task.details.result.project_id}/reviews/${task.details.result.run_id}`}>查看评审进度</Link> : null}</div> : null}</div></article>; })}</div>
       </>}
     </div>
     <form className="codex-composer" onSubmit={submit}><textarea value={content} onChange={(event) => setContent(event.target.value)} placeholder="交给 PRD Pal 一个产品任务…" rows="1" onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submit(event); }}}/><button type="submit" disabled={busy || !content.trim()} aria-label="发送任务">{busy ? <IconLoader2 className="spin" size={18}/> : <IconArrowUp size={18}/>}</button>{error ? <p role="alert">{error}</p> : null}</form>
   </section>
 </main>;
}
