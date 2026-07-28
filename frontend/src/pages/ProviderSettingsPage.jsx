import { useEffect, useMemo, useState } from 'react';
import {
  createModelPreset,
  createProviderConnection,
  deleteModelPreset,
  deleteProviderConnection,
  fetchProviderCatalog,
  listModelPresets,
  listProviderConnections,
  testProviderConnection,
  updateModelPreset,
  updateProviderConnection,
} from '../api';

const emptyPreset = {
  name: '',
  connection_id: '',
  fast_model: '',
  smart_model: '',
  strategic_model: '',
  temperature: 0.2,
  reasoning_effort: 'medium',
  is_default: false,
};

function formatInstallHint(error) {
  const detail = error?.payload?.detail;
  if (typeof detail === 'object' && detail !== null) {
    const parts = [detail.message, detail.install_hint && `安装：${detail.install_hint}`, detail.requires_package && `包：${detail.requires_package}`].filter(Boolean);
    return parts.join(' · ');
  }
  return error?.message || '请求失败';
}

function buildConnectionPayload(catalogEntry, form) {
  const payload = { name: form.name, provider: form.provider, api_key: '', base_url: '', extra: {} };
  (catalogEntry?.fields || []).forEach((field) => {
    const value = String(form[field.name] ?? '').trim();
    if (!value) return;
    if (field.storage === 'api_key') payload.api_key = value;
    else if (field.storage === 'base_url') payload.base_url = value;
    else payload.extra[field.name] = value;
  });
  return payload;
}

function connectionFormFromEntry(connection, catalogEntry) {
  const form = { name: connection.name, provider: connection.provider };
  (catalogEntry?.fields || []).forEach((field) => {
    if (field.storage === 'base_url') form[field.name] = connection.base_url || '';
    else if (field.storage === 'extra') form[field.name] = connection.extra?.[field.name] || '';
    else form[field.name] = '';
  });
  return form;
}

export default function ProviderSettingsPage() {
  const [catalog, setCatalog] = useState([]);
  const [connections, setConnections] = useState([]);
  const [presets, setPresets] = useState([]);
  const [master, setMaster] = useState(false);
  const [message, setMessage] = useState('');
  const [createForm, setCreateForm] = useState({ name: '', provider: 'openai' });
  const [editId, setEditId] = useState('');
  const [editForm, setEditForm] = useState(null);
  const [presetForm, setPresetForm] = useState(emptyPreset);
  const [editPresetId, setEditPresetId] = useState('');

  const catalogById = useMemo(() => Object.fromEntries(catalog.map((item) => [item.id, item])), [catalog]);
  const selectedCreate = catalogById[createForm.provider];

  async function load() {
    try {
      const [catalogRes, connectionsRes, presetsRes] = await Promise.all([
        fetchProviderCatalog(),
        listProviderConnections(),
        listModelPresets(),
      ]);
      setCatalog(catalogRes.providers || []);
      setConnections(connectionsRes.connections || []);
      setMaster(Boolean(connectionsRes.master_key_configured));
      setPresets(presetsRes.presets || []);
      setMessage('');
    } catch (error) {
      setMessage(error.message);
    }
  }

  useEffect(() => {
    load();
  }, []);

  function updateCreateField(name, value) {
    setCreateForm((current) => ({ ...current, [name]: value }));
  }

  async function submitCreate(event) {
    event.preventDefault();
    try {
      const entry = catalogById[createForm.provider];
      await createProviderConnection(buildConnectionPayload(entry, createForm));
      setMessage('连接已保存；密钥不会再显示。');
      setCreateForm({ name: '', provider: createForm.provider });
      await load();
    } catch (error) {
      setMessage(formatInstallHint(error));
    }
  }

  function startEdit(connection) {
    const entry = catalogById[connection.provider];
    setEditId(connection.id);
    setEditForm(connectionFormFromEntry(connection, entry));
  }

  async function submitEdit(event) {
    event.preventDefault();
    if (!editId || !editForm) return;
    try {
      const entry = catalogById[editForm.provider];
      const payload = { name: editForm.name };
      (entry?.fields || []).forEach((field) => {
        const value = String(editForm[field.name] ?? '').trim();
        if (field.storage === 'api_key') {
          if (value) payload.api_key = value;
        } else if (field.storage === 'base_url') {
          payload.base_url = value;
        } else {
          payload.extra = payload.extra || {};
          payload.extra[field.name] = value;
        }
      });
      await updateProviderConnection(editId, payload);
      setMessage('连接已更新。');
      setEditId('');
      setEditForm(null);
      await load();
    } catch (error) {
      setMessage(formatInstallHint(error));
    }
  }

  async function removeConnection(connectionId) {
    if (!window.confirm('删除此连接？关联的模型预设可能失效。')) return;
    try {
      await deleteProviderConnection(connectionId);
      setMessage('连接已删除。');
      await load();
    } catch (error) {
      setMessage(error.message);
    }
  }

  async function test(connectionId) {
    try {
      const result = await testProviderConnection(connectionId);
      setMessage(result.message || '验证成功。');
      await load();
    } catch (error) {
      setMessage(formatInstallHint(error));
    }
  }

  async function submitPreset(event) {
    event.preventDefault();
    try {
      if (editPresetId) {
        await updateModelPreset(editPresetId, presetForm);
        setMessage('模型预设已更新。');
      } else {
        await createModelPreset(presetForm);
        setMessage('模型预设已创建。');
      }
      setEditPresetId('');
      setPresetForm(emptyPreset);
      await load();
    } catch (error) {
      setMessage(error.message);
    }
  }

  function startPresetEdit(preset) {
    setEditPresetId(preset.id);
    setPresetForm({
      name: preset.name,
      connection_id: preset.connection_id,
      fast_model: preset.fast_model,
      smart_model: preset.smart_model,
      strategic_model: preset.strategic_model,
      temperature: preset.temperature,
      reasoning_effort: preset.reasoning_effort,
      is_default: Boolean(preset.is_default),
    });
  }

  async function removePreset(presetId) {
    if (!window.confirm('删除此模型预设？')) return;
    try {
      await deleteModelPreset(presetId);
      setMessage('模型预设已删除。');
      await load();
    } catch (error) {
      setMessage(error.message);
    }
  }

  async function setDefaultPreset(presetId) {
    const preset = presets.find((item) => item.id === presetId);
    if (!preset) return;
    try {
      await updateModelPreset(presetId, {
        name: preset.name,
        connection_id: preset.connection_id,
        fast_model: preset.fast_model,
        smart_model: preset.smart_model,
        strategic_model: preset.strategic_model,
        temperature: preset.temperature,
        reasoning_effort: preset.reasoning_effort,
        is_default: true,
      });
      setMessage('默认模型预设已更新。');
      await load();
    } catch (error) {
      setMessage(error.message);
    }
  }

  function renderProviderFields(form, onChange, entry, { includeSecrets = false } = {}) {
    return (entry?.fields || []).map((field) => {
      if (field.type === 'secret' && !includeSecrets) {
        return (
          <label key={field.name} className="field">
            <span>{field.label}{field.required ? ' *' : ''}</span>
            <input
              type="password"
              value={form[field.name] || ''}
              onChange={(event) => onChange(field.name, event.target.value)}
              placeholder={includeSecrets ? '' : '留空则保持不变'}
              required={field.required && includeSecrets}
            />
          </label>
        );
      }
      return (
        <label key={field.name} className="field">
          <span>{field.label}{field.required ? ' *' : ''}</span>
          <input
            type={field.type === 'secret' ? 'password' : 'text'}
            value={form[field.name] || ''}
            onChange={(event) => onChange(field.name, event.target.value)}
            required={field.required && includeSecrets}
          />
        </label>
      );
    });
  }

  return (
    <main className="stack project-space-page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Settings</p>
          <h1>模型连接</h1>
          <p className="page-lead">你的密钥仅加密保存在当前自托管实例中，不会写入项目或评审产物。</p>
        </div>
      </header>

      {!master ? (
        <p className="feedback-banner feedback-error">
          保存云端 Provider 前，请在 `.env` 中设置 `MARRDP_SECRETS_MASTER_KEY`（Fernet 密钥）。Ollama 无需密钥。
        </p>
      ) : null}

      {message ? <p className="panel-copy">{message}</p> : null}

      <section className="workspace-grid">
        <form className="panel stack" onSubmit={submitCreate}>
          <h2>新建连接</h2>
          <label className="field">
            <span>Provider</span>
            <select value={createForm.provider} onChange={(event) => setCreateForm({ name: createForm.name, provider: event.target.value })}>
              {catalog.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}{item.available ? '' : '（需安装依赖）'}
                </option>
              ))}
            </select>
          </label>
          {selectedCreate && !selectedCreate.available ? (
            <p className="form-error">
              需要安装 {selectedCreate.requires_package} · {selectedCreate.install_hint}
            </p>
          ) : null}
          <label className="field">
            <span>显示名称</span>
            <input value={createForm.name} onChange={(event) => updateCreateField('name', event.target.value)} placeholder="我的模型连接" />
          </label>
          {renderProviderFields(createForm, updateCreateField, selectedCreate, { includeSecrets: true })}
          <button className="primary-button">保存连接</button>
        </form>

        <section className="panel">
          <h2>已保存的连接</h2>
          <ul className="stack-list">
            {connections.map((connection) => {
              const entry = catalogById[connection.provider];
              const editing = editId === connection.id;
              return (
                <li key={connection.id}>
                  {editing ? (
                    <form className="stack" onSubmit={submitEdit}>
                      <strong>编辑 {connection.name}</strong>
                      <label className="field">
                        <span>显示名称</span>
                        <input value={editForm.name} onChange={(event) => setEditForm({ ...editForm, name: event.target.value })} />
                      </label>
                      {renderProviderFields(editForm, (name, value) => setEditForm({ ...editForm, [name]: value }), entry)}
                      <div className="action-row">
                        <button className="primary-button" type="submit">保存</button>
                        <button className="ghost-button" type="button" onClick={() => { setEditId(''); setEditForm(null); }}>取消</button>
                      </div>
                    </form>
                  ) : (
                    <>
                      <strong>{connection.name}</strong>
                      <p>{connection.provider} · {connection.status} · {connection.api_key_masked || '本地连接'}</p>
                      {Object.keys(connection.extra || {}).length ? (
                        <p className="inline-meta">{Object.entries(connection.extra).map(([key, value]) => `${key}: ${value}`).join(' · ')}</p>
                      ) : null}
                      <div className="action-row">
                        <button className="ghost-button" type="button" onClick={() => test(connection.id)}>验证配置</button>
                        <button className="ghost-button" type="button" onClick={() => startEdit(connection)}>编辑</button>
                        <button className="ghost-button" type="button" onClick={() => removeConnection(connection.id)}>删除</button>
                      </div>
                    </>
                  )}
                </li>
              );
            })}
            {!connections.length ? <li>尚未配置连接。</li> : null}
          </ul>
        </section>
      </section>

      <section className="workspace-grid">
        <form className="panel stack" onSubmit={submitPreset}>
          <h2>{editPresetId ? '编辑模型预设' : '新建模型预设'}</h2>
          <label className="field">
            <span>名称</span>
            <input value={presetForm.name} onChange={(event) => setPresetForm({ ...presetForm, name: event.target.value })} required />
          </label>
          <label className="field">
            <span>连接</span>
            <select value={presetForm.connection_id} onChange={(event) => setPresetForm({ ...presetForm, connection_id: event.target.value })} required>
              <option value="">选择连接</option>
              {connections.map((connection) => (
                <option key={connection.id} value={connection.id}>{connection.name} ({connection.provider})</option>
              ))}
            </select>
          </label>
          <label className="field"><span>Fast model</span><input value={presetForm.fast_model} onChange={(event) => setPresetForm({ ...presetForm, fast_model: event.target.value })} required /></label>
          <label className="field"><span>Smart model</span><input value={presetForm.smart_model} onChange={(event) => setPresetForm({ ...presetForm, smart_model: event.target.value })} required /></label>
          <label className="field"><span>Strategic model</span><input value={presetForm.strategic_model} onChange={(event) => setPresetForm({ ...presetForm, strategic_model: event.target.value })} required /></label>
          <label className="field"><span>Temperature</span><input type="number" min="0" max="2" step="0.1" value={presetForm.temperature} onChange={(event) => setPresetForm({ ...presetForm, temperature: Number(event.target.value) })} /></label>
          <label className="field">
            <span>Reasoning effort</span>
            <select value={presetForm.reasoning_effort} onChange={(event) => setPresetForm({ ...presetForm, reasoning_effort: event.target.value })}>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
            </select>
          </label>
          <label className="field checkbox-field">
            <input type="checkbox" checked={presetForm.is_default} onChange={(event) => setPresetForm({ ...presetForm, is_default: event.target.checked })} />
            <span>设为默认预设</span>
          </label>
          <div className="action-row">
            <button className="primary-button">{editPresetId ? '更新预设' : '创建预设'}</button>
            {editPresetId ? <button className="ghost-button" type="button" onClick={() => { setEditPresetId(''); setPresetForm(emptyPreset); }}>取消</button> : null}
          </div>
        </form>

        <section className="panel">
          <h2>模型预设</h2>
          <ul className="stack-list">
            {presets.map((preset) => (
              <li key={preset.id}>
                <strong>{preset.name}{preset.is_default ? ' · 默认' : ''}</strong>
                <p>{preset.fast_model} / {preset.smart_model} / {preset.strategic_model}</p>
                <div className="action-row">
                  {!preset.is_default ? <button className="ghost-button" type="button" onClick={() => setDefaultPreset(preset.id)}>设为默认</button> : null}
                  <button className="ghost-button" type="button" onClick={() => startPresetEdit(preset)}>编辑</button>
                  <button className="ghost-button" type="button" onClick={() => removePreset(preset.id)}>删除</button>
                </div>
              </li>
            ))}
            {!presets.length ? <li>尚未创建模型预设。</li> : null}
          </ul>
        </section>
      </section>
    </main>
  );
}
