import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { api, ApiError, errorMessage } from '../api';
import type { Department, ManagedUser, Page, Role, User, UserState } from '../types';
import { ErrorBox, Pagination, roleNames } from '../ui';
import { useUnsaved } from '../useUnsaved';
import { availableDepartments } from '../components/DefinitionFields';
import './Users.css';

type Draft = { username: string; name: string; password: string; role: Role; department: string; active: boolean };
const newDraft = (): Draft => ({ username: '', name: '', password: '', role: 'registrar', department: '', active: true });
const userState = (user: ManagedUser): UserState => ({ display_name: user.display_name, role: user.role, department_id: user.department_id, is_active: user.is_active });
const draftFor = (user: ManagedUser): Draft => ({ username: user.username, name: user.display_name, password: '', role: user.role, department: user.department_id === null ? '' : String(user.department_id), active: user.is_active });
const roleHelp: Record<Role, string> = {
  admin: '维护工作人员账号，管理体检流程。',
  registrar: '登记受检者、创建体检，查看检查进度。',
  doctor: '查看并录入本人所属科室的检查结果。',
  reviewer: '查看汇总结果、填写总检结论和查看报告。',
};

export default function Users({ user, onSelfUpdated }: { user: User; onSelfUpdated: (user: User) => void }) {
  const [data, setData] = useState<Page<ManagedUser>>({ items: [], total: 0, page: 1, page_size: 20 });
  const [page, setPage] = useState(1);
  const [searchInput, setSearchInput] = useState(''); const [query, setQuery] = useState('');
  const [refresh, setRefresh] = useState(0); const [loading, setLoading] = useState(false);
  const [listError, setListError] = useState(''); const [departments, setDepartments] = useState<Department[]>([]);
  const [departmentError, setDepartmentError] = useState('');
  const [editing, setEditing] = useState<ManagedUser | null>(null);
  const [draft, setDraft] = useState<Draft>(newDraft);
  const [saving, setSaving] = useState(false); const [saveError, setSaveError] = useState(''); const [message, setMessage] = useState('');
  const [conflicted, setConflicted] = useState(false); const [latest, setLatest] = useState<ManagedUser | null>(null);
  const [readingLatest, setReadingLatest] = useState(false);
  const baseline = editing ? draftFor(editing) : newDraft();
  const dirty = Object.keys(draft).some(key => draft[key as keyof Draft] !== baseline[key as keyof Draft]);
  const self = editing?.id === user.id;
  useUnsaved(dirty);

  useEffect(() => {
    let active = true; setLoading(true); setListError('');
    api<Page<ManagedUser>>(`/users?page=${page}&page_size=20&q=${encodeURIComponent(query)}`)
      .then(result => { if (active) setData(result); })
      .catch(error => { if (active) setListError(errorMessage(error)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [page, query, refresh]);
  useEffect(() => {
    let active = true; setDepartmentError('');
    api<Department[]>('/departments').then(result => { if (active) setDepartments(result); })
      .catch(error => { if (active) setDepartmentError(errorMessage(error)); });
    return () => { active = false; };
  }, [refresh]);

  function openEditor(target: ManagedUser | null) {
    if (saving || readingLatest) return;
    if (dirty && !window.confirm('当前账号资料尚未保存。确认放弃本次修改？')) return;
    setEditing(target); setDraft(target ? draftFor(target) : newDraft());
    setSaveError(''); setMessage(''); setConflicted(false); setLatest(null);
  }
  async function readLatest() {
    if (!editing) return;
    setReadingLatest(true); setSaveError('');
    try { setLatest(await api<ManagedUser>(`/users/${editing.id}`)); }
    catch (error) { setSaveError(errorMessage(error)); }
    finally { setReadingLatest(false); }
  }
  function acceptLatest() {
    if (!latest) return;
    setEditing(latest); setLatest(null); setConflicted(false); setSaveError('');
    setMessage('已取得最新资料，您的输入仍保留。请核对表单后保存修改。');
  }
  async function save(event: FormEvent) {
    event.preventDefault(); setSaveError(''); setMessage(''); setSaving(true);
    const state: UserState = { display_name: draft.name.trim(), role: draft.role, department_id: draft.role === 'doctor' ? Number(draft.department) : null, is_active: draft.active };
    try {
      const saved = await api<ManagedUser>(editing ? `/users/${editing.id}` : '/users', {
        method: editing ? 'PUT' : 'POST',
        body: editing ? { ...state, expected: userState(editing) } : {
          username: draft.username.trim(), display_name: state.display_name,
          password: draft.password, role: state.role, department_id: state.department_id,
        },
      });
      if (saved.id === user.id) onSelfUpdated(saved);
      setConflicted(false); setLatest(null);
      if (editing) { setEditing(saved); setDraft(draftFor(saved)); setMessage('账号修改已保存。权限变更后，该工作人员需重新登录。'); }
      else { setEditing(null); setDraft(newDraft()); setPage(1); setMessage('账号创建成功。请将账号交给对应工作人员使用。'); }
      setRefresh(value => value + 1);
    } catch (error) {
      setSaveError(errorMessage(error));
      if (error instanceof ApiError && error.status === 409) {
        setRefresh(value => value + 1);
        if (editing) { setConflicted(true); setLatest(null); }
      }
    } finally { setSaving(false); }
  }
  const departmentName = (id: number | null) => id === null ? '—' : departments.find(entry => entry.id === id)?.name || '科室名称暂未加载';
  const allowedDepartments = availableDepartments(departments, editing?.role === 'doctor' && draft.role === 'doctor' ? editing.department_id : null);
  const canSave = dirty && Boolean(draft.name.trim()) && (editing !== null || Boolean(draft.username.trim() && draft.password.length >= 12))
    && !conflicted && (draft.role !== 'doctor' || Boolean(draft.department && !departmentError && allowedDepartments.some(department => department.id === Number(draft.department))));
  const busy = saving || readingLatest;

  return <>
    <div className="page-heading"><div><p className="eyebrow">工作人员权限</p><h1>账号管理</h1><p className="muted">按岗位分配账号和权限；医生需指定所属科室。</p></div><button disabled={busy} onClick={() => openEditor(null)}>新建账号</button></div>
    {message && <div className="notice success" role="status">{message}</div>}
    <div className="registration-layout users-layout">
      <section className="card user-editor">
        <div className="section-header"><h2>{editing ? '编辑账号' : '新建工作人员账号'}</h2></div>
        <form onSubmit={save}>
          {editing ? <p className="user-account-name">账号：<strong>{editing.username}</strong>{self && <span className="badge">当前账号</span>}</p> : <label>账号 *<input disabled={saving} required minLength={3} maxLength={64} pattern="[A-Za-z0-9_.\-]{3,64}" autoComplete="off" value={draft.username} onChange={event => setDraft({ ...draft, username: event.target.value })} /><span className="field-hint">3～64 位字母、数字、点、下划线或连字符，区分大小写。</span></label>}
          <label>工作人员姓名 *<input disabled={saving} required maxLength={100} value={draft.name} onChange={event => setDraft({ ...draft, name: event.target.value })} /></label>
          {!editing && <label>初始密码 *<input disabled={saving} required type="password" minLength={12} maxLength={1024} autoComplete="new-password" value={draft.password} onChange={event => setDraft({ ...draft, password: event.target.value })} /><span className="field-hint">至少 12 个字符，每位工作人员使用独立密码。</span></label>}
          <label>岗位角色 *<select disabled={saving || self} value={draft.role} onChange={event => setDraft({ ...draft, role: event.target.value as Role, department: '' })}>{(Object.keys(roleNames) as Role[]).map(role => <option key={role} value={role}>{roleNames[role]}</option>)}</select></label>
          <p className="field-hint user-role-description">{roleHelp[draft.role]}</p>
          {draft.role === 'doctor' && <label>所属科室 *<select required disabled={saving || Boolean(departmentError)} value={draft.department} onChange={event => setDraft({ ...draft, department: event.target.value })}><option value="">请选择科室</option>{allowedDepartments.map(department => <option key={department.id} value={department.id}>{department.name}{department.is_active === false ? '（已停用，保留原所属）' : ''}</option>)}</select></label>}
          <ErrorBox message={departmentError} />
          {editing && <label className="checkbox-row"><input type="checkbox" disabled={saving || self} checked={draft.active} onChange={event => setDraft({ ...draft, active: event.target.checked })} />启用账号</label>}
          {self ? <p className="field-hint">为保留管理入口，当前账号的角色和启用状态不能在此变更。</p> : editing && <p className="field-hint">调整角色、科室或启用状态后，原登录会话会失效。停用账号保留历史操作记录。</p>}
          <ErrorBox message={saveError} />
          {conflicted && <div className="user-conflict" role="status">
            <p>您的输入已保留。请先读取并核对服务器最新资料，再决定是否保存当前修改。</p>
            <button type="button" disabled={busy} onClick={readLatest}>{readingLatest ? '正在读取…' : '读取最新资料（保留输入）'}</button>
            {latest && <><dl><dt>最新姓名</dt><dd>{latest.display_name}</dd><dt>最新角色</dt><dd>{roleNames[latest.role]}</dd><dt>最新科室</dt><dd>{departmentName(latest.department_id)}</dd><dt>最新状态</dt><dd>{latest.is_active ? '启用' : '停用'}</dd></dl><button type="button" disabled={busy} onClick={acceptLatest}>已核对，保留输入继续修改</button></>}
          </div>}
          <div className="form-actions"><button type="button" disabled={busy || (!editing && !dirty)} onClick={() => openEditor(null)}>取消编辑</button><button className="primary" disabled={busy || !canSave}>{saving ? '正在保存…' : editing ? '保存修改' : '创建账号'}</button></div>
        </form>
      </section>
      <section className="card list-card">
        <div className="section-header"><h2>工作人员账号</h2><button disabled={loading || saving} onClick={() => setRefresh(value => value + 1)}>刷新列表</button></div>
        <form className="search-bar" onSubmit={event => { event.preventDefault(); setPage(1); setQuery(searchInput.trim()); setRefresh(value => value + 1); }}><input aria-label="搜索工作人员" placeholder="输入账号或工作人员姓名" maxLength={100} value={searchInput} onChange={event => setSearchInput(event.target.value)} /><button className="primary" disabled={loading}>查询</button></form>
        <ErrorBox message={listError} />
        <div className="table-scroll"><table aria-label="工作人员账号列表" aria-busy={loading}><thead><tr><th>账号 / 姓名</th><th>角色</th><th>所属科室</th><th>状态</th><th className="align-right">操作</th></tr></thead><tbody>
          {data.items.map(account => <tr key={account.id}><td><strong>{account.username}</strong><small className="cell-sub">{account.display_name}{account.id === user.id ? ' · 当前账号' : ''}</small></td><td>{roleNames[account.role]}</td><td>{departmentName(account.department_id)}</td><td><span className={`badge ${account.is_active ? 'done' : 'waiting'}`}>{account.is_active ? '启用' : '停用'}</span></td><td className="align-right"><button className="link-button" disabled={busy} aria-label={`编辑 ${account.username}`} onClick={() => openEditor(account)}>编辑权限</button></td></tr>)}
          {data.items.length === 0 && <tr><td className="empty" colSpan={5}>{loading ? '正在加载账号…' : listError ? '账号加载失败，请重试' : '暂无符合条件的账号'}</td></tr>}
        </tbody></table></div>
        <Pagination page={page} total={data.total} busy={loading} onPage={setPage} />
      </section>
    </div>
  </>;
}
