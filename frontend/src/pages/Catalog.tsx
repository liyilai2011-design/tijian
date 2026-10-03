import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { api, ApiError, errorMessage, requestId } from '../api';
import type { CatalogRecord, Department, DepartmentRecord, Page } from '../types';
import { ErrorBox, Pagination } from '../ui';
import { useUnsaved } from '../useUnsaved';
import { blankDefinition, canSubmitDefinition, catalogPayload, confirmedDefinition, confirmedDefinitionSave, DefinitionFields, definitionDraft, definitionMatches, departmentCreatePayload, departmentPayload, resultTypeNames } from '../components/DefinitionFields';
import './Catalog.css';

type Definition = DepartmentRecord | CatalogRecord;
type Kind = 'departments' | 'catalog-items';
type Submission = { fingerprint: string; path: string; method: string; body: Record<string, unknown>; fields: Record<string, unknown>; id?: number; version?: number };
function RecordFacts({ record, catalog }: { record: Definition; catalog: boolean }) {
  return <dl className="definition-facts"><dt>名称</dt><dd>{record.name}</dd><dt>状态</dt><dd>{record.is_active ? '启用' : '停用'}</dd><dt>版本</dt><dd>{record.version}</dd>{catalog && 'department_id' in record && <><dt>科室</dt><dd>{record.department_name}</dd><dt>分类</dt><dd>{record.category || '—'}</dd><dt>单位</dt><dd>{record.unit || '—'}</dd><dt>参考范围</dt><dd>{record.reference_range || '—'}</dd><dt>结果类型</dt><dd>{resultTypeNames[record.result_type]}</dd><dt>异常标记</dt><dd>{record.abnormal_allowed ? '允许' : '不允许'}</dd></>}</dl>;
}

function DefinitionManager({ kind, onLocked }: { kind: Kind; onLocked: (locked: boolean) => void }) {
  const catalog = kind === 'catalog-items'; const label = catalog ? '检查项目' : '科室'; const basePath = `/admin/${kind}`;
  const [data, setData] = useState<Page<Definition>>({ items: [], total: 0, page: 1, page_size: 20 });
  const [page, setPage] = useState(1); const [query, setQuery] = useState(''); const [queryInput, setQueryInput] = useState('');
  const [activeFilter, setActiveFilter] = useState(''); const [departmentFilter, setDepartmentFilter] = useState('');
  const [refresh, setRefresh] = useState(0); const [loading, setLoading] = useState(false); const [listError, setListError] = useState('');
  const [departments, setDepartments] = useState<Department[]>([]); const [departmentError, setDepartmentError] = useState('');
  const [editing, setEditing] = useState<Definition | null>(null); const [draft, setDraft] = useState(blankDefinition);
  const [saving, setSaving] = useState(false); const [confirming, setConfirming] = useState(false); const [saveError, setSaveError] = useState(''); const [message, setMessage] = useState('');
  const [savedMessage, setSavedMessage] = useState(false);
  const [conflict, setConflict] = useState(false); const [latest, setLatest] = useState<Definition | null>(null); const [reading, setReading] = useState(false);
  const submission = useRef<Submission | null>(null); const inFlight = useRef(false);
  const baseline = editing ? definitionDraft(editing) : blankDefinition();
  const draftFields = catalog ? catalogPayload(draft) : departmentPayload(draft);
  const dirty = JSON.stringify(draftFields) !== JSON.stringify(catalog ? catalogPayload(baseline) : departmentPayload(baseline));
  const busy = saving || reading; const locked = busy || confirming;
  useUnsaved(dirty || confirming, locked, '保存结果尚未确认，请保持当前页面并核对服务器结果，避免重复创建或覆盖资料。');
  useEffect(() => { onLocked(locked); return () => onLocked(false); }, [locked, onLocked]);
  useEffect(() => {
    let active = true; setLoading(true); setListError('');
    const params = new URLSearchParams({ page: String(page), page_size: '20', q: query });
    if (activeFilter) params.set('is_active', activeFilter);
    if (catalog && departmentFilter) params.set('department_id', departmentFilter);
    api<Page<Definition>>(`${basePath}?${params}`).then(value => { if (active) setData(value); })
      .catch(failure => { if (active) setListError(errorMessage(failure)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [page, query, activeFilter, departmentFilter, refresh, basePath, catalog]);
  useEffect(() => {
    if (!catalog) return;
    let active = true; setDepartmentError('');
    api<Department[]>('/departments').then(value => { if (active) setDepartments(value); }).catch(failure => { if (active) setDepartmentError(errorMessage(failure)); });
    return () => { active = false; };
  }, [catalog, refresh]);
  function open(record: Definition | null) {
    if (locked) return;
    if (dirty && !window.confirm(`当前${label}资料尚未保存，确认放弃本次输入？`)) return;
    setEditing(record); setDraft(record ? definitionDraft(record) : blankDefinition());
    submission.current = null; setSaveError(''); setMessage(''); setSavedMessage(false); setConflict(false); setLatest(null);
  }
  function finish(record: Definition, creationChanged = false) {
    setEditing(record); setDraft(definitionDraft(record)); submission.current = null;
    setConfirming(false); setConflict(false); setLatest(null); setSaveError('');
    setMessage(`${label}已保存，服务器已确认。${record.is_active ? '' : '停用后历史体检及结果仍保留。'}${creationChanged ? '服务器当前资料与原提交不同，可能已有其他管理员修改，请核对当前表单。' : ''}`); setSavedMessage(true); setRefresh(value => value + 1);
  }
  async function readLatest() {
    if (!editing || reading || saving) return;
    setReading(true); setSaveError(''); setSavedMessage(false);
    try {
      const record = await api<Definition>(`${basePath}/${editing.id}`);
      if (!confirmedDefinition(record, catalog) || record.id !== editing.id) throw new Error('服务器资料暂时无法核对，请重新读取。');
      const original = submission.current;
      if (confirming && original && record.version > (original.version ?? 0) && definitionMatches(record, original.fields, catalog)) { finish(record); return; }
      setLatest(record);
      if (confirming && original && record.version > (original.version ?? 0)) { setConflict(true); setMessage('服务器已有更新版本，当前输入仍保留。请核对最新资料后再决定如何修改。'); }
      else if (confirming) setMessage('服务器当前仍为原版本，保存结果尚未确认。请保持原输入并重试保存。');
    } catch (failure) { setSaveError(errorMessage(failure)); } finally { setReading(false); }
  }
  function acceptLatest() {
    if (!latest || !conflict || busy) return;
    setEditing(latest); setLatest(null); setConflict(false); setConfirming(false); submission.current = null; setSaveError('');
    setMessage('已核对服务器最新版本，您的输入已保留。请核对表单后保存修改。'); setSavedMessage(false);
  }
  async function save(event: FormEvent) {
    event.preventDefault(); if (inFlight.current || reading || (conflict && !confirming)) return;
    if (confirming && !submission.current) { setSaveError('原保存请求暂时无法核对，请保持当前页面，不要重新创建记录。'); return; }
    inFlight.current = true; setSaving(true); setSaveError(''); setMessage(''); setSavedMessage(false);
    try {
      if (!confirming) {
        const fields = catalog ? catalogPayload(draft) : departmentPayload(draft);
        const fingerprint = JSON.stringify({ id: editing?.id, version: editing?.version, fields });
        if (!submission.current || submission.current.fingerprint !== fingerprint) {
          const creatingFields = catalog ? fields : departmentCreatePayload(draft);
          submission.current = { fingerprint, fields, path: editing ? `${basePath}/${editing.id}` : basePath, method: editing ? 'PUT' : 'POST',
            body: editing ? { ...fields, version: editing.version } : { ...creatingFields, request_id: requestId() }, id: editing?.id, version: editing?.version };
        }
      }
      const original = submission.current!;
      const record = await api<Definition>(original.path, { method: original.method, body: original.body });
      if (!confirmedDefinitionSave(record, catalog, original.id === undefined ? undefined : { id: original.id, version: original.version!, fields: original.fields })) throw new ApiError('服务器响应尚未确认保存结果，请保持原输入并重试核对。', 200, true);
      finish(record, original.id === undefined && !definitionMatches(record, original.fields, catalog));
    } catch (failure) {
      setSaveError(errorMessage(failure));
      if (failure instanceof ApiError && failure.uncertain) setConfirming(true);
      if (failure instanceof ApiError && failure.status === 409 && editing) { setConflict(true); setLatest(null); }
    } finally { inFlight.current = false; setSaving(false); }
  }
  const canSave = canSubmitDefinition(draft, catalog, { confirming, hasSubmission: submission.current !== null, dirty, conflict, departmentError });
  return <>
    {message && <div className={`notice ${savedMessage ? 'success' : ''}`} role="status">{message}</div>}
    <div className="definition-layout">
      <section className="card definition-editor"><div className="section-header"><h2>{editing ? `编辑${label}` : `新增${label}`}</h2>{editing && <span className="muted">版本 {editing.version}</span>}</div>
        <form onSubmit={save}><DefinitionFields draft={draft} onChange={setDraft} disabled={locked} catalog={catalog} departments={departments} editing={Boolean(editing)} />
          <p className="field-hint">停用后不用于新体检，已有体检及结果保留。修改基础项目不改写已登记体检的历史资料。</p>
          <ErrorBox message={departmentError} />{departmentError && <button type="button" disabled={locked} onClick={() => setRefresh(value => value + 1)}>重新读取科室</button>}
          <ErrorBox message={saveError} />
          {confirming && <div className="notice" role="status">保存结果尚未确认，请保持原输入重试核对。核对前不能切换资料或创建新记录。</div>}
          {(conflict || (confirming && editing)) && <div className="definition-conflict" role="status"><p>当前输入已保留。请读取服务器资料，核对版本及已保存的内容。</p><button type="button" disabled={busy} onClick={() => void readLatest()}>{reading ? '正在核对…' : '读取服务器资料（保留输入）'}</button>{latest && <><RecordFacts record={latest} catalog={catalog} />{conflict && (!confirming || latest.version > (submission.current?.version ?? 0)) && <button type="button" disabled={busy} onClick={acceptLatest}>已核对，保留输入继续修改</button>}</>}</div>}
          <div className="form-actions"><button type="button" disabled={locked || (!editing && !dirty)} onClick={() => open(null)}>取消编辑</button><button className="primary" disabled={busy || !canSave}>{saving ? '正在保存并核对…' : confirming ? '重试并核对保存' : editing ? '保存修改' : `新增${label}`}</button></div>
        </form>
      </section>
      <section className="card list-card"><div className="section-header"><h2>{label}列表</h2><div className="definition-actions"><button disabled={locked} onClick={() => open(null)}>{`新增${label}`}</button><button disabled={loading || busy} onClick={() => setRefresh(value => value + 1)}>刷新列表</button></div></div>
        <form className="definition-filters" onSubmit={event => { event.preventDefault(); setPage(1); setQuery(queryInput.trim()); setRefresh(value => value + 1); }}><input aria-label={`搜索${label}`} maxLength={100} placeholder={`输入${label}名称${catalog ? '或分类' : ''}`} value={queryInput} onChange={event => setQueryInput(event.target.value)} /><select aria-label={`${label}状态筛选`} value={activeFilter} onChange={event => { setPage(1); setActiveFilter(event.target.value); }}><option value="">全部状态</option><option value="true">启用</option><option value="false">停用</option></select>{catalog && <select aria-label="项目科室筛选" value={departmentFilter} onChange={event => { setPage(1); setDepartmentFilter(event.target.value); }}><option value="">全部科室</option>{departments.map(department => <option key={department.id} value={department.id}>{department.name}{department.is_active === false ? '（停用）' : ''}</option>)}</select>}<button className="primary" disabled={loading}>查询</button></form>
        <ErrorBox message={listError} />
        <div className="table-scroll"><table aria-label={`${label}列表`} aria-busy={loading}><thead><tr><th>名称{catalog ? ' / 分类' : ''}</th>{catalog && <><th>科室</th><th>类型 / 异常标记</th><th>单位 / 参考范围</th></>}<th>状态</th><th className="align-right">操作</th></tr></thead><tbody>{data.items.map(record => <tr key={record.id}><td><strong>{record.name}</strong>{'category' in record && <small className="cell-sub">{record.category || '未分类'}</small>}</td>{catalog && 'department_id' in record && <><td>{record.department_name}{record.department_is_active === false && <small className="cell-sub">科室已停用</small>}</td><td>{resultTypeNames[record.result_type]}<small className="cell-sub">{record.abnormal_allowed ? '可标记异常' : '不标记异常'}</small></td><td>{record.unit || '—'}<small className="cell-sub">{record.reference_range || '—'}</small></td></>}<td><span className={`badge ${record.is_active ? 'done' : 'waiting'}`}>{record.is_active ? '启用' : '停用'}</span></td><td className="align-right"><button className="link-button" disabled={locked} aria-label={`编辑${label} ${record.name}`} onClick={() => open(record)}>编辑 / 启停</button></td></tr>)}{data.items.length === 0 && <tr><td className="empty" colSpan={catalog ? 6 : 3}>{loading ? '正在加载…' : listError ? '列表加载失败，请重试' : '暂无符合条件的记录'}</td></tr>}</tbody></table></div><Pagination page={page} total={data.total} busy={loading} onPage={setPage} />
      </section>
    </div>
  </>;
}
export default function Catalog() {
  const [kind, setKind] = useState<Kind>('departments'); const [locked, setLocked] = useState(false);
  function switchKind(next: Kind) { if (locked || next === kind) return; if (window.dispatchEvent(new CustomEvent('before-page-change', { cancelable: true }))) setKind(next); }
  return <><div className="page-heading"><div><p className="eyebrow">基础资料</p><h1>科室和检查项目</h1><p className="muted">维护科室与检查项目，停用保留历史。本阶段使用现有套餐。</p></div></div><div className="definition-tabs" aria-label="基础资料分类"><button disabled={locked} className={kind === 'departments' ? 'primary' : ''} onClick={() => switchKind('departments')}>科室</button><button disabled={locked} className={kind === 'catalog-items' ? 'primary' : ''} onClick={() => switchKind('catalog-items')}>检查项目</button></div><DefinitionManager key={kind} kind={kind} onLocked={setLocked} /></>;
}
