import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { api, ApiError, errorMessage, requestId } from '../api';
import type { PackageDetail, PackageSummary, Page } from '../types';
import { ErrorBox, Pagination } from '../ui';
import { useUnsaved } from '../useUnsaved';
import { blankPackage, canSubmitPackage, confirmedManagedPackage, confirmedPackageSave, PackageFields, packageDraft, packageMatches, packagePayload } from '../components/PackageFields';
import { PackageItemSelector } from '../components/PackageItemSelector';
import './Catalog.css';
import './Packages.css';

type Submission = { fingerprint: string; path: string; method: string; fields: ReturnType<typeof packagePayload>; body: Record<string, unknown>; id?: number; version?: number };
function PackageFacts({ record }: { record: PackageDetail }) {
  return <div><dl className="definition-facts"><dt>名称</dt><dd>{record.name}</dd><dt>说明</dt><dd className="pre-wrap">{record.description || '—'}</dd><dt>状态</dt><dd>{record.is_active ? '启用' : '停用'}</dd><dt>版本</dt><dd>{record.version}</dd><dt>项目数</dt><dd>{record.item_count}</dd><dt>登记可用</dt><dd>{record.is_available ? '可用' : record.unavailable_reason || '暂不可用'}</dd></dl><details><summary>服务器已保存的检查项目</summary><ul className="package-fact-items">{record.items.map(item => <li key={item.id}>{item.name} · {item.department_name}{!item.is_active || item.department_is_active === false ? ' · 已停用' : ''}</li>)}</ul></details></div>;
}
export default function Packages() {
  const [data, setData] = useState<Page<PackageSummary>>({ items: [], total: 0, page: 1, page_size: 20 }); const [page, setPage] = useState(1);
  const [query, setQuery] = useState(''); const [queryInput, setQueryInput] = useState(''); const [activeFilter, setActiveFilter] = useState('');
  const [refresh, setRefresh] = useState(0); const [loading, setLoading] = useState(false); const [listError, setListError] = useState('');
  const [editing, setEditing] = useState<PackageDetail | null>(null); const [draft, setDraft] = useState(blankPackage);
  const [saving, setSaving] = useState(false); const [reading, setReading] = useState(false); const [confirming, setConfirming] = useState(false);
  const [conflict, setConflict] = useState(false); const [latest, setLatest] = useState<PackageDetail | null>(null); const [saveError, setSaveError] = useState('');
  const [message, setMessage] = useState(''); const [savedMessage, setSavedMessage] = useState(false);
  const submission = useRef<Submission | null>(null); const inFlight = useRef(false);
  const baseline = editing ? packageDraft(editing) : blankPackage(); const dirty = JSON.stringify(packagePayload(draft)) !== JSON.stringify(packagePayload(baseline));
  const busy = saving || reading; const locked = busy || confirming;
  useUnsaved(dirty || confirming, locked, '套餐保存结果尚未确认，请保持当前页面并核对服务器结果，避免重复创建或覆盖资料。');
  useEffect(() => {
    let active = true; setLoading(true); setListError(''); const params = new URLSearchParams({ page: String(page), page_size: '20', q: query }); if (activeFilter) params.set('is_active', activeFilter);
    api<Page<PackageSummary>>(`/admin/packages?${params}`).then(value => { if (active) setData(value); }).catch(failure => { if (active) setListError(errorMessage(failure)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [page, query, activeFilter, refresh]);
  async function open(record: PackageSummary | null) {
    if (locked) return; if (dirty && !window.confirm('当前套餐资料尚未保存，确认放弃本次输入？')) return;
    if (!record) { setEditing(null); setDraft(blankPackage()); submission.current = null; setSaveError(''); setMessage(''); setSavedMessage(false); setConflict(false); setLatest(null); return; }
    setReading(true); setSaveError('');
    try {
      const detail = await api<PackageDetail>(`/admin/packages/${record.id}`); if (!confirmedManagedPackage(detail) || detail.id !== record.id) throw new Error('套餐详情暂时无法核对，请重试读取。');
      setEditing(detail); setDraft(packageDraft(detail)); submission.current = null; setSaveError(''); setMessage(''); setSavedMessage(false); setConflict(false); setLatest(null);
    } catch (failure) { setSaveError(errorMessage(failure)); } finally { setReading(false); }
  }
  function finish(record: PackageDetail, creationChanged = false) {
    setEditing(record); setDraft(packageDraft(record)); submission.current = null; setConfirming(false); setConflict(false); setLatest(null); setSaveError('');
    setSavedMessage(true); setMessage(`套餐已保存，服务器已确认。${record.is_active ? '' : '停用后历史体检和报告仍保留。'}${creationChanged ? '服务器当前资料与原提交不同，可能已有其他管理员修改，请核对当前表单。' : ''}`); setRefresh(value => value + 1);
  }
  async function readLatest() {
    if (!editing || busy) return; setReading(true); setSaveError(''); setSavedMessage(false);
    try {
      const record = await api<PackageDetail>(`/admin/packages/${editing.id}`); if (!confirmedManagedPackage(record) || record.id !== editing.id) throw new Error('服务器套餐资料暂时无法核对，请重试读取。');
      const original = submission.current;
      if (confirming && original && record.version > (original.version ?? 0) && packageMatches(record, original.fields)) { finish(record); return; }
      setLatest(record);
      if (confirming && original && record.version > (original.version ?? 0)) { setConflict(true); setMessage('服务器已有更新版本，当前输入仍保留。请核对最新套餐内容后再决定如何修改。'); }
      else if (confirming) setMessage('服务器当前仍为原版本，保存结果尚未确认。请保持原输入并重试保存。');
    } catch (failure) { setSaveError(errorMessage(failure)); } finally { setReading(false); }
  }
  function acceptLatest() {
    if (!latest || !conflict || busy) return; setEditing(latest); setLatest(null); setConflict(false); setConfirming(false); submission.current = null; setSaveError(''); setSavedMessage(false);
    setMessage('已核对服务器最新版本，您的名称、说明及所选项目仍保留。请核对后保存修改。');
  }
  async function save(event: FormEvent) {
    event.preventDefault(); if (inFlight.current || reading || (conflict && !confirming)) return;
    if (confirming && !submission.current) { setSaveError('原保存请求暂时无法核对，请保持当前页面，不要重复创建套餐。'); return; }
    if (!canSubmitPackage(draft, editing, { confirming, hasSubmission: submission.current !== null, dirty, conflict })) return;
    inFlight.current = true; setSaving(true); setSaveError(''); setMessage(''); setSavedMessage(false);
    try {
      if (!confirming) {
        const fields = packagePayload(draft); const fingerprint = JSON.stringify({ id: editing?.id, version: editing?.version, fields });
        if (!submission.current || submission.current.fingerprint !== fingerprint) submission.current = { fingerprint, fields, path: editing ? `/admin/packages/${editing.id}` : '/admin/packages', method: editing ? 'PUT' : 'POST', body: editing ? { ...fields, version: editing.version } : { ...fields, request_id: requestId() }, id: editing?.id, version: editing?.version };
      }
      const original = submission.current!; const record = await api<PackageDetail>(original.path, { method: original.method, body: original.body });
      if (!confirmedPackageSave(record, original.id === undefined ? undefined : { id: original.id, version: original.version!, fields: original.fields })) throw new ApiError('套餐保存结果尚未确认，请保持原输入并重试核对。', 200, true);
      finish(record, original.id === undefined && !packageMatches(record, original.fields));
    } catch (failure) { setSaveError(errorMessage(failure)); if (failure instanceof ApiError && failure.uncertain) setConfirming(true); if (failure instanceof ApiError && failure.status === 409 && editing) { setConflict(true); setLatest(null); } }
    finally { inFlight.current = false; setSaving(false); }
  }
  const canSave = canSubmitPackage(draft, editing, { confirming, hasSubmission: submission.current !== null, dirty, conflict });
  return <><div className="page-heading"><div><p className="eyebrow">基础资料</p><h1>套餐管理</h1><p className="muted">维护套餐名称、说明和检查项目。修改套餐不改写已登记体检及历史报告。</p></div><button disabled={locked} onClick={() => void open(null)}>新增套餐</button></div>
    {message && <div className={`notice ${savedMessage ? 'success' : ''}`} role="status">{message}</div>}
    <div className="package-admin-layout"><section className="card package-editor"><div className="section-header"><h2>{editing ? '编辑套餐' : '新增套餐'}</h2>{editing && <span className="muted">版本 {editing.version}</span>}</div><form onSubmit={save}><PackageFields draft={draft} disabled={locked} onChange={setDraft} /><PackageItemSelector key={editing?.id ?? 'new'} items={draft.items} disabled={locked} onChange={items => setDraft(previous => ({ ...previous, items }))} />
      {!editing && draft.items.length === 0 && <p className="field-hint">新增套餐至少选择 1 个检查项目，最多 200 个。</p>}{editing && !editing.is_available && <div className="notice">当前套餐暂不可用于新登记：{editing.unavailable_reason || '成员或科室已停用'}</div>}<ErrorBox message={saveError} />
      {confirming && <div className="notice" role="status">保存结果尚未确认，请保持原名称、说明及项目集合，恢复连接后重试核对。核对前不能切换套餐或创建新记录。</div>}
      {(conflict || (confirming && editing)) && <div className="definition-conflict" role="status"><p>当前输入已保留，请读取服务器资料并核对名称、说明和项目集合。</p><button type="button" disabled={busy} onClick={() => void readLatest()}>{reading ? '正在读取…' : '读取最新套餐（保留输入）'}</button>{latest && <><PackageFacts record={latest} />{conflict && (!confirming || latest.version > (submission.current?.version ?? 0)) && <button type="button" disabled={busy} onClick={acceptLatest}>已核对，保留输入继续修改</button>}</>}</div>}
      <div className="form-actions"><button type="button" disabled={locked || (!editing && !dirty)} onClick={() => void open(null)}>取消编辑</button><button className="primary" disabled={busy || !canSave}>{saving ? '正在保存并核对…' : confirming ? '重试并核对保存' : editing ? '保存修改' : '新增套餐'}</button></div>
    </form></section><section className="card list-card"><div className="section-header"><h2>套餐列表</h2><button disabled={loading || busy} onClick={() => setRefresh(value => value + 1)}>刷新列表</button></div><form className="definition-filters" onSubmit={event => { event.preventDefault(); setPage(1); setQuery(queryInput.trim()); setRefresh(value => value + 1); }}><input aria-label="搜索管理套餐" placeholder="输入套餐名称" maxLength={100} value={queryInput} onChange={event => setQueryInput(event.target.value)} /><select aria-label="套餐状态筛选" value={activeFilter} onChange={event => { setPage(1); setActiveFilter(event.target.value); }}><option value="">全部状态</option><option value="true">启用</option><option value="false">停用</option></select><button className="primary" disabled={loading}>查询</button></form><ErrorBox message={listError} /><div className="table-scroll"><table aria-label="套餐管理列表" aria-busy={loading}><thead><tr><th>套餐名称 / 说明</th><th>项目数</th><th>启用状态 / 登记可用</th><th className="align-right">操作</th></tr></thead><tbody>{data.items.map(record => <tr key={record.id}><td><strong>{record.name}</strong>{record.description && <small className="cell-sub">{record.description}</small>}</td><td>{record.item_count}</td><td><span className={`badge ${record.is_active ? 'done' : 'waiting'}`}>{record.is_active ? '启用' : '停用'}</span><small className="cell-sub">{record.is_available ? '可用于登记' : record.unavailable_reason || '暂不可用于登记'}</small></td><td className="align-right"><button className="link-button" disabled={locked} aria-label={`编辑套餐 ${record.name}`} onClick={() => void open(record)}>编辑 / 启停</button></td></tr>)}{data.items.length === 0 && <tr><td className="empty" colSpan={4}>{loading ? '正在加载套餐…' : listError ? '套餐加载失败，请重试' : '暂无符合条件的套餐。'}</td></tr>}</tbody></table></div><Pagination page={page} total={data.total} busy={loading} onPage={setPage} /></section></div>
  </>;
}
