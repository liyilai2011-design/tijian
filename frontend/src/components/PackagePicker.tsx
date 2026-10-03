import { useEffect, useRef, useState } from 'react';
import { api, errorMessage } from '../api';
import type { PackageDetail, PackageSummary, Page } from '../types';
import { ErrorBox, Pagination } from '../ui';
import './PackagePicker.css';

export function confirmedPackageSelection(value: unknown, id: number): value is PackageDetail {
  if (!value || typeof value !== 'object') return false;
  const detail = value as Partial<PackageDetail>;
  return Boolean(detail.id === id && Number.isSafeInteger(detail.version) && (detail.version ?? 0) > 0 && typeof detail.name === 'string'
    && detail.is_available === true && detail.is_active === true && Array.isArray(detail.items) && detail.items.length > 0 && detail.items.length <= 200 && detail.item_count === detail.items.length
    && Array.isArray(detail.item_ids) && detail.item_ids.length === detail.items.length && new Set(detail.item_ids).size === detail.item_ids.length && new Set(detail.items.map(item => item.id)).size === detail.items.length
    && detail.items.every(item => Number.isSafeInteger(item.id) && item.id > 0 && detail.item_ids!.includes(item.id)));
}
export function PackagePicker({ value, version, disabled, onChange, reloadKey = 0 }: {
  value: string; version: number | null; disabled: boolean; onChange: (id: string, version: number | null) => void; reloadKey?: number;
}) {
  const [open, setOpen] = useState(false); const [data, setData] = useState<Page<PackageSummary>>({ items: [], total: 0, page: 1, page_size: 20 });
  const [page, setPage] = useState(1); const [query, setQuery] = useState(''); const [queryInput, setQueryInput] = useState(''); const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(false); const [listError, setListError] = useState(''); const [detail, setDetail] = useState<PackageDetail | null>(null);
  const [reading, setReading] = useState(false); const [detailError, setDetailError] = useState(''); const [detailRefresh, setDetailRefresh] = useState(0);
  const onChangeRef = useRef(onChange); onChangeRef.current = onChange;
  useEffect(() => {
    if (!open) return;
    let active = true; setLoading(true); setListError('');
    api<Page<PackageSummary>>(`/registration/packages?page=${page}&page_size=20&q=${encodeURIComponent(query)}`).then(result => { if (active) setData(result); })
      .catch(failure => { if (active) setListError(errorMessage(failure)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [open, page, query, refresh]);
  useEffect(() => {
    if (!value) { setDetail(null); setDetailError(''); setReading(false); return; }
    let active = true; setReading(true); setDetailError('');
    onChangeRef.current(value, null);
    api<PackageDetail>(`/registration/packages/${Number(value)}`).then(result => {
      if (!active) return;
      const unavailableReason = result?.unavailable_reason;
      if (!confirmedPackageSelection(result, Number(value))) throw new Error(unavailableReason || '套餐详情暂时无法核对，请重试读取或暂不选择套餐。');
      setDetail(result); onChangeRef.current(value, result.version);
    }).catch(failure => { if (active) setDetailError(errorMessage(failure)); }).finally(() => { if (active) setReading(false); });
    return () => { active = false; };
  }, [value, detailRefresh, reloadKey]);
  function choose(id: string) { if (disabled || reading) return; setDetail(null); setDetailError(''); onChangeRef.current(id, null); if (id === value) setDetailRefresh(count => count + 1); }
  function reread() { if (disabled || reading || !value) return; onChangeRef.current(value, null); setDetailRefresh(count => count + 1); }
  function search() { setPage(1); setQuery(queryInput.trim()); setRefresh(count => count + 1); }
  return <div className="package-picker">
    <div className="section-header"><strong>体检套餐（选填）</strong><button type="button" disabled={disabled} onClick={() => setOpen(previous => !previous)}>{open ? '收起套餐选择' : '选择套餐'}</button></div>
    {value ? <div className="package-selection"><strong>{detail?.id === Number(value) ? detail.name : '正在核对所选套餐'}</strong><div className="reader-actions"><button type="button" disabled={disabled || reading} onClick={reread}>重新读取套餐</button><button type="button" disabled={disabled || reading} onClick={() => choose('')}>暂不选择套餐</button></div></div> : <p className="field-hint">暂不选择套餐。保存后为待检，暂无检查项目。</p>}
    <ErrorBox message={detailError} />{reading && <p className="field-hint" role="status">正在读取套餐项目，请核对后再登记。</p>}
    {detail && detail.id === Number(value) && version !== null && <div className="package-preview"><strong>{detail.name} · {detail.item_count} 个检查项目</strong>{detail.description && <p className="muted">{detail.description}</p>}<ul>{detail.items.map(item => <li key={item.id}><span>{item.name}</span><small>{item.department_name}</small></li>)}</ul></div>}
    {open && <div className="package-search-results"><div className="package-search"><input disabled={disabled} aria-label="搜索登记套餐" maxLength={100} placeholder="输入套餐名称" value={queryInput} onChange={event => setQueryInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); search(); } }} /><button type="button" disabled={disabled || loading} onClick={search}>查询</button><button type="button" disabled={disabled || loading} onClick={() => setRefresh(count => count + 1)}>刷新</button></div><ErrorBox message={listError} />
      {data.items.map(summary => <div className="package-option" key={summary.id}><div><strong>{summary.name}</strong><small>{summary.item_count} 个项目{summary.description ? ` · ${summary.description}` : ''}</small></div><button type="button" disabled={disabled || reading || !summary.is_available} onClick={() => choose(String(summary.id))}>{String(summary.id) === value ? '已选择' : '选择'}</button></div>)}
      {data.items.length === 0 && <p className="field-hint">{loading ? '正在加载套餐…' : listError ? '套餐加载失败，其他资料已保留。' : '暂无符合条件的可用套餐，可暂不选择。'}</p>}<Pagination page={page} total={data.total} busy={disabled || loading || reading} onPage={setPage} />
    </div>}
  </div>;
}
