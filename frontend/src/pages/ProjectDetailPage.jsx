import { useEffect, useMemo, useState } from 'react';
import { Link, useParams, useNavigate } from 'react-router-dom';
import {
  addProjectSource,
  createProjectReview,
  deleteProjectSource,
  diffProjectSources,
  getProject,
  getProjectSource,
  getProjectTimeline,
  listModelPresets,
  rollbackProjectSource,
  updateProject,
  uploadProjectSource,
} from '../api';

const EVENT_LABELS = {
  source_added: '添加资料',
  source_uploaded: '上传资料',
  source_updated: '更新资料',
  source_deleted: '删除资料',
  source_rollback: '回滚资料',
  review: '发起评审',
};

function groupSourcesByTitle(sources) {
  const groups = {};
  sources.forEach((s) => {
    if (!groups[s.title]) groups[s.title] = [];
    groups[s.title].push(s);
  });
  Object.values(groups).forEach((list) => list.sort((a, b) => b.version - a.version));
  return groups;
}

export default function ProjectDetailPage() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const [project, setProject] = useState(null);
  const [tab, setTab] = useState('overview');
  const [error, setError] = useState('');
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [url, setUrl] = useState('');
  const [timeline, setTimeline] = useState([]);
  const [uploadFile, setUploadFile] = useState(null);
  const [selectedSource, setSelectedSource] = useState(null);
  const [viewContent, setViewContent] = useState('');
  const [diffAgainst, setDiffAgainst] = useState('');
  const [diffText, setDiffText] = useState('');
  const [busy, setBusy] = useState(false);
  const [presets, setPresets] = useState([]);
  const [modelPresetId, setModelPresetId] = useState('');

  async function load() {
    try {
      const [p, timelineRes, presetsRes] = await Promise.all([
        getProject(projectId),
        getProjectTimeline(projectId),
        listModelPresets(),
      ]);
      setProject(p);
      setTimeline(timelineRes.events || []);
      setPresets(presetsRes.presets || []);
      setModelPresetId(p.model_preset_id || '');
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => { load(); }, [projectId]);

  const sourceGroups = useMemo(
    () => (project ? groupSourcesByTitle(project.sources) : {}),
    [project],
  );

  async function addSource(e) {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      await addProjectSource(projectId, {
        title,
        content,
        source_url: url,
        source_type: url ? 'link' : 'prd_text',
        is_prd: true,
        parent_source_id: selectedSource?.id || undefined,
      });
      setTitle('');
      setContent('');
      setUrl('');
      setSelectedSource(null);
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleUpload(e) {
    e.preventDefault();
    if (!uploadFile) return;
    setBusy(true);
    setError('');
    try {
      await uploadProjectSource(projectId, uploadFile, {
        title,
        parentSourceId: selectedSource?.id || '',
      });
      setUploadFile(null);
      setTitle('');
      setSelectedSource(null);
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function viewSource(sourceId) {
    setError('');
    try {
      const detail = await getProjectSource(projectId, sourceId);
      setViewContent(detail.content || '');
      setSelectedSource(detail);
      setDiffText('');
      setDiffAgainst('');
    } catch (e) {
      setError(e.message);
    }
  }

  async function runDiff(sourceId, againstId) {
    if (!againstId) return;
    setError('');
    try {
      const result = await diffProjectSources(projectId, sourceId, againstId);
      setDiffText(result.diff || '(无差异)');
    } catch (e) {
      setError(e.message);
    }
  }

  async function rollback(sourceId) {
    setBusy(true);
    setError('');
    try {
      await rollbackProjectSource(projectId, sourceId);
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function removeSource(sourceId) {
    if (!window.confirm('确定删除此版本？')) return;
    setBusy(true);
    setError('');
    try {
      await deleteProjectSource(projectId, sourceId);
      if (selectedSource?.id === sourceId) {
        setSelectedSource(null);
        setViewContent('');
        setDiffText('');
      }
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function run(sourceId) {
    try {
      const payload = { source_id: sourceId };
      if (modelPresetId) payload.model_preset_id = modelPresetId;
      const r = await createProjectReview(projectId, payload);
      navigate(`/projects/${projectId}/reviews/${r.run_id}`);
    } catch (e) {
      setError(e.message);
    }
  }

  async function saveModelPreset(value) {
    setModelPresetId(value);
    try {
      await updateProject(projectId, { model_preset_id: value || null });
      await load();
    } catch (e) {
      setError(e.message);
    }
  }

  if (!project) return <main className="panel">正在加载项目…</main>;

  const tabs = [['overview', '概览'], ['sources', '资料'], ['reviews', 'PRD 与评审'], ['delivery', '协作与交付']];

  return (
    <main className="stack project-space-page">
      <header className="page-header">
        <div>
          <Link to="/" className="inline-meta">← 所有项目</Link>
          <p className="eyebrow">Project Space</p>
          <h1>{project.name}</h1>
          <p className="page-lead">{project.description || '添加资料后即可开始可追溯的 PRD 评审。'}</p>
        </div>
      </header>

      <nav className="workbench-tabs">
        {tabs.map(([id, label]) => (
          <button
            key={id}
            className={`workbench-tab${tab === id ? ' workbench-tab-active' : ''}`}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </nav>

      {error ? <p className="form-error">{error}</p> : null}

      {tab === 'overview' ? (
        <section className="workspace-grid">
          <article className="panel">
            <h2>当前状态</h2>
            <p className="metric-value">{project.sources.length} <small>份资料</small></p>
            <p>将资料与每次评审保留在同一个项目上下文。</p>
            <label className="field">
              <span>模型预设</span>
              <select value={modelPresetId} onChange={(e) => saveModelPreset(e.target.value)}>
                <option value="">使用实例默认</option>
                {presets.map((preset) => (
                  <option key={preset.id} value={preset.id}>
                    {preset.name}{preset.is_default ? '（默认）' : ''}
                  </option>
                ))}
              </select>
            </label>
          </article>
          <article className="panel">
            <h2>最近活动</h2>
            <ul className="stack-list">
              {timeline.slice(0, 8).map((e, i) => (
                <li key={i}>
                  <strong>{EVENT_LABELS[e.kind] || e.kind}</strong>
                  <p>{e.label}</p>
                </li>
              ))}
              {!timeline.length ? <li>暂无活动</li> : null}
            </ul>
          </article>
        </section>
      ) : null}

      {tab === 'sources' ? (
        <section className="workspace-grid">
          <form className="panel stack" onSubmit={addSource}>
            <h2>添加项目资料</h2>
            <label className="field">
              <span>标题</span>
              <input value={title} onChange={(e) => setTitle(e.target.value)} required placeholder="会员改版 PRD" />
            </label>
            <label className="field">
              <span>正文</span>
              <textarea value={content} onChange={(e) => setContent(e.target.value)} placeholder="粘贴 PRD、会议纪要或补充要求" />
            </label>
            <label className="field">
              <span>飞书或文档链接</span>
              <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://..." />
            </label>
            {selectedSource ? <p className="inline-meta">将作为 {selectedSource.title} v{selectedSource.version} 的新版本</p> : null}
            <button className="primary-button" disabled={busy}>保存为新版本</button>
          </form>

          <form className="panel stack" onSubmit={handleUpload}>
            <h2>上传文件</h2>
            <p className="inline-meta">支持 .md / .txt / .pdf / .docx</p>
            <label className="field">
              <span>文件</span>
              <input type="file" accept=".md,.txt,.pdf,.docx" onChange={(e) => setUploadFile(e.target.files?.[0] || null)} required />
            </label>
            <label className="field">
              <span>标题（可选）</span>
              <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="默认使用文件名" />
            </label>
            <button className="primary-button" disabled={busy || !uploadFile}>上传并保存</button>
          </form>

          <section className="panel">
            <h2>已连接资料</h2>
            {Object.entries(sourceGroups).map(([groupTitle, versions]) => (
              <article key={groupTitle} className="stack" style={{ marginBottom: '1.5rem' }}>
                <h3>{groupTitle}</h3>
                <ul className="stack-list">
                  {versions.map((s) => (
                    <li key={s.id}>
                      <strong>v{s.version}</strong>
                      <p>{s.source_type} · {s.created_at?.slice(0, 10)}</p>
                      {s.metadata?.validation && !s.metadata.validation.valid ? (
                        <p className="form-error">校验: {s.metadata.validation.issues.join(', ')}</p>
                      ) : null}
                      <div className="action-row">
                        <button type="button" onClick={() => viewSource(s.id)}>查看内容</button>
                        <button type="button" onClick={() => setSelectedSource(s)}>基于此新建版本</button>
                        <button type="button" onClick={() => rollback(s.id)} disabled={busy}>回滚到此版本</button>
                        <button type="button" onClick={() => removeSource(s.id)} disabled={busy}>删除</button>
                      </div>
                    </li>
                  ))}
                </ul>
              </article>
            ))}
            {!project.sources.length ? <p>暂无资料。</p> : null}
          </section>

          {selectedSource ? (
            <section className="panel stack">
              <h2>{selectedSource.title} v{selectedSource.version}</h2>
              <pre style={{ whiteSpace: 'pre-wrap', maxHeight: '320px', overflow: 'auto' }}>{viewContent || '点击「查看内容」加载正文'}</pre>
              <label className="field">
                <span>与另一版本对比</span>
                <select value={diffAgainst} onChange={(e) => setDiffAgainst(e.target.value)}>
                  <option value="">选择版本</option>
                  {(sourceGroups[selectedSource.title] || [])
                    .filter((s) => s.id !== selectedSource.id)
                    .map((s) => (
                      <option key={s.id} value={s.id}>v{s.version}</option>
                    ))}
                </select>
              </label>
              <button type="button" onClick={() => runDiff(selectedSource.id, diffAgainst)} disabled={!diffAgainst}>生成 diff</button>
              {diffText ? <pre style={{ whiteSpace: 'pre-wrap', maxHeight: '240px', overflow: 'auto', fontSize: '0.85rem' }}>{diffText}</pre> : null}
            </section>
          ) : null}
        </section>
      ) : null}

      {tab === 'reviews' ? (
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>PRD 与评审</h2>
              <p>选择一份资料发起新的审查。</p>
            </div>
          </div>
          <ul className="stack-list">
            {project.sources.map((s) => (
              <li key={s.id}>
                <strong>{s.title} v{s.version}</strong>
                <div className="action-row">
                  <button className="primary-button" onClick={() => run(s.id)}>开始评审</button>
                </div>
              </li>
            ))}
            {project.runs.map((r) => (
              <li key={r.run_id}>
                <Link to={`/projects/${projectId}/reviews/${r.run_id}`}>
                  <strong>{r.run_id}</strong>
                </Link>
                <p>查看此次评审结果、澄清和产物。</p>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {tab === 'delivery' ? (
        <section className="panel stack">
          <h2>协作与交付</h2>
          <p>在评审详情中处理澄清、修订、roadmap 和 handoff。所有产物会自动保留在该项目的运行记录中。</p>
          <ul className="stack-list">
            {project.runs.map((r) => {
              const linked = project.sources.find((s) => s.id === r.source_id);
              return (
                <li key={r.run_id}>
                  <Link to={`/projects/${projectId}/reviews/${r.run_id}`}>
                    <strong>{r.run_id}</strong>
                  </Link>
                  <p>
                    {linked ? `资料: ${linked.title} v${linked.version}` : '资料已删除或未关联'}
                    {' · '}
                    澄清 / 修订 / 产物可在评审页查看
                  </p>
                </li>
              );
            })}
            {!project.runs.length ? <li>尚无评审记录。先在「PRD 与评审」发起审查。</li> : null}
          </ul>
        </section>
      ) : null}
    </main>
  );
}
