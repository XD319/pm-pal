import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

const QUICK_ACTIONS = [
  ['读取飞书文档', '请读取并整理这份飞书文档：'],
  ['汇总反馈', '请汇总当前产品的用户反馈，并生成机会草案'],
  ['生成 PRD', '请根据当前上下文生成一份 PRD 草案'],
  ['发起评审', '请对当前 PRD 发起评审'],
];

async function agentRequest(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload?.detail?.message || '请求失败，请稍后重试。');
  return payload;
}

export default function AgentHomePage() {
  const [params, setParams] = useSearchParams();
  const productId = params.get('product_id') || '';
  const [conversation, setConversation] = useState(null);
  const [content, setContent] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function ensureConversation() {
    if (conversation) return conversation;
    const payload = await agentRequest('/api/agent/conversations', {
      method: 'POST', body: JSON.stringify({ product_id: productId }),
    });
    setConversation(payload);
    return payload;
  }

  useEffect(() => { ensureConversation().catch((err) => setError(err.message)); }, []);

  async function submit(event) {
    event?.preventDefault();
    if (!content.trim()) return;
    setBusy(true); setError('');
    try {
      const current = await ensureConversation();
      const next = await agentRequest(`/api/agent/conversations/${current.conversation.id}/messages`, {
        method: 'POST', body: JSON.stringify({ content }),
      });
      setConversation(next); setContent('');
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  }

  async function confirm(taskId, confirmed) {
    setBusy(true); setError('');
    try {
      await agentRequest(`/api/agent/tasks/${taskId}/confirm`, { method: 'POST', body: JSON.stringify({ confirmed, actor: params.get('open_id') || 'local' }) });
      const next = await agentRequest(`/api/agent/conversations/${conversation.conversation.id}`);
      setConversation(next);
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  }

  const messages = conversation?.messages || [];
  const tasksById = Object.fromEntries((conversation?.tasks || []).map((task) => [task.id, task]));
  return (
    <main className="agent-home">
      <header className="agent-hero">
        <div><p className="eyebrow">PRD Pal Agent</p><h1>把产品工作交给一个懂上下文的搭档</h1><p>从飞书文档、反馈和会议材料开始；我会整理证据、起草 PRD，并在关键节点等你确认。</p></div>
      </header>
      <section className="agent-layout">
        <div className="agent-chat panel">
          <div className="agent-chat-header"><div><p className="section-kicker">对话</p><h2>今天想推进什么？</h2></div><Link className="secondary-button" to={`/workbench${productId ? `?product_id=${encodeURIComponent(productId)}` : ''}`}>打开工作台</Link></div>
          <div className="agent-messages" aria-live="polite">
            {!messages.length ? <div className="agent-welcome"><strong>从一份飞书文档开始</strong><p>粘贴飞书链接，或直接告诉我需要整理反馈、生成 PRD、发起评审。</p></div> : null}
            {messages.map((message) => {
              const task = message.payload?.task_id ? tasksById[message.payload.task_id] : null;
              return <article key={message.id} className={`agent-message agent-message-${message.role}`}><strong>{message.role === 'user' ? '你' : 'PRD Pal'}</strong><p>{message.content}</p>{message.payload?.references?.map((ref) => <a key={ref.url} className="agent-reference" href={ref.url} target="_blank" rel="noreferrer">飞书原文 ↗</a>)}{task ? <div className="agent-task"><strong>{task.title}</strong><p>{task.details?.next_step}</p>{task.status === 'awaiting_confirmation' ? <div className="action-row"><button className="primary-button" disabled={busy} onClick={() => confirm(task.id, true)}>确认执行</button><button className="ghost-button" disabled={busy} onClick={() => confirm(task.id, false)}>暂不处理</button></div> : task.status === 'failed' ? <div className="agent-task-error"><p>{task.details?.error || '执行未完成。'}</p><div className="action-row"><button className="primary-button" disabled={busy} onClick={() => confirm(task.id, true)}>重新尝试</button><Link className="ghost-button" to="/settings/providers">检查连接配置</Link></div></div> : <><span className="status-badge status-completed">{task.status === 'completed' ? '已完成' : '已忽略'}</span>{task.details?.result?.run_id ? <Link className="agent-result-link" to={`/projects/${task.details.result.project_id}/reviews/${task.details.result.run_id}`}>查看评审进度 →</Link> : null}{task.details?.result?.workbench_path ? <Link className="agent-result-link" to={task.details.result.workbench_path}>前往交付工作台 →</Link> : null}</>}</div> : null}</article>;
            })}
          </div>
          <form className="agent-composer" onSubmit={submit}><textarea value={content} onChange={(e) => setContent(e.target.value)} placeholder="例如：读取这份飞书 PRD，找出遗漏并生成修订草案" rows="3" /><button className="primary-button" disabled={busy || !content.trim()}>{busy ? '处理中…' : '发送给 Agent'}</button></form>
          {error ? <p className="form-error">{error}</p> : null}
        </div>
        <aside className="agent-aside panel"><p className="section-kicker">常用起点</p><h2>选择一项开始</h2><div className="agent-quick-actions">{QUICK_ACTIONS.map(([label, prompt]) => <button key={label} className="ghost-button" onClick={() => setContent(prompt)}>{label}</button>)}</div><div className="agent-source-card"><strong>飞书优先</strong><p>已连接的文档会保留原文链接、同步状态与版本记录。手动上传仍可在项目资料页使用。</p></div></aside>
      </section>
    </main>
  );
}
