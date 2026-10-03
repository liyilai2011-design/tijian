import { useEffect, useState } from 'react';
import { api, errorMessage } from '../api';
import type { Department, Exam, Page, User } from '../types';
import { dateTime, ErrorBox, Pagination, sexName, Status, statusNames } from '../ui';

export default function Exams({ user, onOpen }: { user: User; onOpen: (id: number) => void }) {
  const doctor = user.role === 'doctor', reviewer = user.role === 'reviewer';
  const [data, setData] = useState<Page<Exam>>({ items: [], total: 0, page: 1, page_size: 20 });
  const [departments, setDepartments] = useState<Department[]>([]);
  const [page, setPage] = useState(1); const [queryInput, setQueryInput] = useState(''); const [query, setQuery] = useState('');
  const [status, setStatus] = useState(reviewer ? 'ready_for_review' : ''); const [department, setDepartment] = useState('');
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [refresh, setRefresh] = useState(0);
  const [updated, setUpdated] = useState('');
  useEffect(() => {
    let active = true; setBusy(true); setError('');
    const params = new URLSearchParams({ page: String(page), page_size: '20', q: query });
    if (status) params.set('status', status); if (department) params.set('department_id', department);
    api<Page<Exam>>('/exams?' + params.toString()).then(value => { if (active) { setData(value); setUpdated(new Date().toLocaleTimeString('zh-CN', { hour12: false })); } })
      .catch(err => { if (active) setError(errorMessage(err)); }).finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [page, query, status, department, refresh]);
  useEffect(() => { if (!doctor) void api<Department[]>('/departments').then(setDepartments).catch(() => { /* Optional filter only; table error stays explicit. */ }); }, [doctor]);
  // Only visible work queues refresh; forms and entire pages are never reloaded.
  useEffect(() => {
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') setRefresh(n => n + 1); }, 30000);
    return () => window.clearInterval(timer);
  }, []);
  return <><div className="page-heading"><div><p className="eyebrow">{doctor ? '科室工作' : reviewer ? '总检工作' : '体检工作'}</p><h1>{doctor ? '本科室待检' : reviewer ? '总检工作台' : '体检管理'}</h1><p className="muted">{doctor ? '查看本科室检查项目并录入结果。列表每 30 秒局部更新。' : reviewer ? '汇总已完成的检查结果，填写总检结论并发布报告。' : '所有体检由服务器统一管理，可按条件查找。'}</p></div><button disabled={busy} onClick={() => setRefresh(n => n + 1)}>{busy ? '刷新中…' : '刷新列表'}</button></div><section className="card"><form className="filters" onSubmit={e => { e.preventDefault(); setPage(1); setQuery(queryInput.trim()); setRefresh(n => n + 1); }}><label className="search-filter">姓名 / 体检编号<input placeholder="请输入搜索关键词" maxLength={100} value={queryInput} onChange={e => setQueryInput(e.target.value)} /></label><label>体检状态<select value={status} onChange={e => { setPage(1); setStatus(e.target.value); }}><option value="">{doctor ? '本科室未完成' : '全部状态'}</option>{Object.entries(statusNames).map(([value, name]) => <option value={value} key={value}>{name}</option>)}</select></label>{!doctor && <label>检查科室<select value={department} onChange={e => { setPage(1); setDepartment(e.target.value); }}><option value="">全部科室</option>{departments.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>}<button className="primary" disabled={busy}>查询</button></form><div className="list-meta"><span>体检记录</span><span>{updated ? `更新于 ${updated}` : '等待加载'}{busy ? ' · 加载中' : ''}</span></div><ErrorBox message={error} /><div className="table-scroll"><table aria-busy={busy}><thead><tr><th>体检编号 / 体检日期</th><th>受检者</th><th>体检套餐</th><th>{doctor ? '本科室进度' : '检查进度'}</th><th>状态</th><th className="align-right">操作</th></tr></thead><tbody>{data.items.map(exam => <tr key={exam.id}><td><strong className="number">{exam.exam_number}</strong><small className="cell-sub">{exam.exam_date || '—'} · {exam.exam_type || '个人体检'}</small></td><td><strong>{exam.patient.name}</strong><small className="cell-sub">{sexName(exam.patient.sex)} · {exam.patient.birth_date || '日期未填'}</small></td><td>{exam.package_name}</td><td><span className={exam.total_count > 0 && exam.completed_count === exam.total_count ? 'complete-count' : ''}>{exam.completed_count} / {exam.total_count}</span><small className="cell-sub">项已完成</small></td><td><Status value={exam.status} /></td><td className="align-right"><button className="link-button" onClick={() => onOpen(exam.id)}>{doctor ? '录入结果' : reviewer ? '查看 / 总检' : '查看详情'} →</button></td></tr>)}{data.items.length === 0 && <tr><td className="empty" colSpan={6}>{busy ? '正在加载体检记录…' : error ? '数据加载失败，请重试' : '暂无符合条件的体检记录'}</td></tr>}</tbody></table></div><Pagination page={page} total={data.total} busy={busy} onPage={setPage} /></section></>;
}

