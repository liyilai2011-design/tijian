import { useEffect, useState } from 'react';
import { api, errorMessage } from '../api';
import type { CatalogRecord, Department, Page } from '../types';
import { ErrorBox, Pagination } from '../ui';

export function togglePackageItem(items: CatalogRecord[], item: CatalogRecord): CatalogRecord[] {
  if (items.some(current => current.id === item.id)) return items.filter(current => current.id !== item.id);
  if (items.length >= 200 || !item.is_active || item.department_is_active === false) return items;
  return [...items, item];
}
export function SelectedPackageItems({ items, disabled, onRemove }: { items: CatalogRecord[]; disabled: boolean; onRemove: (id: number) => void }) {
  return <div className="selected-package-items"><div className="section-header"><strong>已选项目 · {items.length} / 200</strong></div>{items.length === 0 ? <p className="field-hint">暂未选择检查项目。</p> : <ul>{items.map(item => <li key={item.id}><div><strong>{item.name}</strong><small>{item.department_name}{!item.is_active || item.department_is_active === false ? ' · 已停用，保留旧成员或移除' : ''}</small></div><button type="button" disabled={disabled} aria-label={`移除项目 ${item.name}`} onClick={() => onRemove(item.id)}>移除</button></li>)}</ul>}</div>;
}
export function PackageItemSelector({ items, disabled, onChange }: { items: CatalogRecord[]; disabled: boolean; onChange: (items: CatalogRecord[]) => void }) {
  const [data, setData] = useState<Page<CatalogRecord>>({ items: [], total: 0, page: 1, page_size: 20 }); const [page, setPage] = useState(1);
  const [query, setQuery] = useState(''); const [queryInput, setQueryInput] = useState(''); const [department, setDepartment] = useState(''); const [activeFilter, setActiveFilter] = useState('true');
  const [departments, setDepartments] = useState<Department[]>([]); const [refresh, setRefresh] = useState(0); const [loading, setLoading] = useState(false); const [error, setError] = useState(''); const [departmentError, setDepartmentError] = useState('');
  useEffect(() => {
    let active = true; setLoading(true); setError(''); const params = new URLSearchParams({ page: String(page), page_size: '20', q: query });
    if (department) params.set('department_id', department); if (activeFilter) params.set('is_active', activeFilter);
    api<Page<CatalogRecord>>(`/admin/catalog-items?${params}`).then(value => { if (active) setData(value); }).catch(failure => { if (active) setError(errorMessage(failure)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [page, query, department, activeFilter, refresh]);
  useEffect(() => {
    let active = true; setDepartmentError(''); api<Department[]>('/departments').then(value => { if (active) setDepartments(value); }).catch(failure => { if (active) setDepartmentError(errorMessage(failure)); });
    return () => { active = false; };
  }, [refresh]);
  function search() { setPage(1); setQuery(queryInput.trim()); setRefresh(value => value + 1); }
  return <div className="package-item-selector"><SelectedPackageItems items={items} disabled={disabled} onRemove={id => onChange(items.filter(item => item.id !== id))} />
    <details><summary>添加检查项目</summary><div className="definition-filters"><input disabled={disabled} aria-label="搜索套餐检查项目" maxLength={100} placeholder="输入项目名称或分类" value={queryInput} onChange={event => setQueryInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); search(); } }} /><select disabled={disabled} aria-label="套餐项目科室筛选" value={department} onChange={event => { setPage(1); setDepartment(event.target.value); }}><option value="">全部科室</option>{departments.map(entry => <option key={entry.id} value={entry.id}>{entry.name}{entry.is_active === false ? '（停用）' : ''}</option>)}</select><select disabled={disabled} aria-label="套餐项目状态筛选" value={activeFilter} onChange={event => { setPage(1); setActiveFilter(event.target.value); }}><option value="true">启用项目</option><option value="">全部状态</option><option value="false">停用项目</option></select><button type="button" disabled={disabled || loading} onClick={search}>查询</button><button type="button" disabled={disabled || loading} onClick={() => setRefresh(value => value + 1)}>刷新项目</button></div>
      <ErrorBox message={departmentError} /><ErrorBox message={error} /><div className="package-item-results">{data.items.map(item => { const selected = items.some(current => current.id === item.id); return <div className="package-option" key={item.id}><div><strong>{item.name}</strong><small>{item.department_name}{item.category ? ` · ${item.category}` : ''}{!item.is_active || item.department_is_active === false ? ' · 已停用' : ''}</small></div><button type="button" disabled={disabled || (!selected && (items.length >= 200 || !item.is_active || item.department_is_active === false))} aria-label={`${selected ? '移除' : '添加'}项目 ${item.name}`} onClick={() => onChange(togglePackageItem(items, item))}>{selected ? '已选 / 移除' : '添加'}</button></div>; })}{data.items.length === 0 && <p className="field-hint">{loading ? '正在加载项目…' : error ? '项目加载失败，已选内容仍保留。' : '暂无符合条件的项目。'}</p>}</div><Pagination page={page} total={data.total} busy={disabled || loading} onPage={setPage} />
    </details>
  </div>;
}
