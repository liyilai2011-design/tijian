import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { api, ApiError, errorMessage } from '../api';
import type { ExamDetail as Detail, ExamItem, History, User } from '../types';
import { dateTime, ErrorBox, Loading, sexName, Status } from '../ui';
import { useUnsaved } from '../useUnsaved';
import { AbnormalControl, ResultValueControl, resultTypeFor, validResultValue } from '../components/ResultValueControl';
import { resultTypeNames } from '../components/DefinitionFields';

function ResultHistory({ item, onClose }: { item: ExamItem; onClose: () => void }) {
  const [entries, setEntries] = useState<History[]>([]); const [busy, setBusy] = useState(true); const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  useEffect(() => { let active = true; setBusy(true); setError(''); void api<History[]>(`/exam-items/${item.id}/history`).then(data => { if (active) setEntries(data); }).catch(err => { if (active) setError(errorMessage(err)); }).finally(() => { if (active) setBusy(false); }); return () => { active = false; }; }, [item.id, refresh]);
  return <div className="modal-overlay"><section className="modal wide-modal" role="dialog" aria-modal="true" aria-labelledby="history-title"><div className="section-header"><div><p className="eyebrow">检查结果修改记录</p><h2 id="history-title">{item.item_name}</h2></div><button onClick={onClose}>关闭</button></div><ErrorBox message={error} />{error && <button onClick={() => setRefresh(n => n + 1)}>重新加载</button>}{busy ? <Loading /> : entries.length ? <div className="history-list">{entries.map(entry => <article key={entry.id} className="history-entry"><div className="section-header"><strong>版本 {entry.version}{entry.is_abnormal && <span className="badge abnormal">异常</span>}</strong><span className="muted">{entry.changed_by} · {dateTime(entry.changed_at)}</span></div><p className="pre-wrap">{entry.value}</p>{entry.note && <p className="muted pre-wrap">备注：{entry.note}</p>}</article>)}</div> : <p className="empty">暂无录入记录</p>}</section></div>;
}

function ResultRow({ item, editable, historyAllowed, onSaved, onDirty, onReload }: { item: ExamItem; editable: boolean; historyAllowed: boolean; onSaved: (item: ExamItem) => void; onDirty: (id: number, dirty: boolean) => void; onReload: () => Promise<void> }) {
  const resultType = resultTypeFor(item); const abnormalAllowed = item.abnormal_allowed !== false;
  const [value, setValue] = useState(item.value || ''); const [abnormal, setAbnormal] = useState(item.is_abnormal); const [note, setNote] = useState(item.note || '');
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [message, setMessage] = useState(''); const [history, setHistory] = useState(false);
  const [conflict, setConflict] = useState(false);
  const lastServer = useRef({ version: item.version, value: item.value || '', abnormal: item.is_abnormal, note: item.note || '' });
  const dirty = editable && (value !== (item.value || '') || abnormal !== item.is_abnormal || note !== (item.note || ''));
  useEffect(() => { onDirty(item.id, dirty); return () => onDirty(item.id, false); }, [dirty, item.id, onDirty]);
  useEffect(() => {
    if (lastServer.current.version !== item.version) {
      const previous = lastServer.current;
      const wasEdited = value !== previous.value || abnormal !== previous.abnormal || note !== previous.note;
      if (!wasEdited && !conflict) {
        setValue(item.value || ''); setAbnormal(item.is_abnormal); setNote(item.note || '');
      }
      lastServer.current = { version: item.version, value: item.value || '', abnormal: item.is_abnormal, note: item.note || '' };
      if (conflict || wasEdited) { setConflict(false); setError(''); setMessage('已取得服务器最新版本，当前输入已保留。请核对服务器结果后再保存。'); }
    }
  }, [item.version, conflict]);
  async function save(event: FormEvent) {
    event.preventDefault(); if (!validResultValue(value, resultType)) { setError(resultType === 'number' ? '请填写有效的有限数字。' : resultType === 'boolean' ? '请选择是或否。' : '请填写检查结果。'); return; } setError(''); setMessage(''); setBusy(true);
    try {
      const updated = await api<ExamItem>(`/exam-items/${item.id}`, { method: 'PUT', body: { value: value.trim(), is_abnormal: abnormalAllowed && abnormal, note: note.trim() || null, version: item.version } });
      lastServer.current = { version: updated.version, value: updated.value || '', abnormal: updated.is_abnormal, note: updated.note || '' };
      setValue(updated.value || ''); setAbnormal(updated.is_abnormal); setNote(updated.note || ''); onSaved(updated); setMessage('保存成功，服务器已确认。');
    } catch (err) { setError(errorMessage(err)); if (err instanceof ApiError && err.status === 409) setConflict(true); } finally { setBusy(false); }
  }
  const serverChanged = dirty && item.status === 'completed';
  return <article className={`result-row ${item.is_abnormal ? 'abnormal-row' : ''}`}><div className="result-heading"><div><h3>{item.item_name}<span className={`badge ${item.status === 'completed' ? 'done' : 'waiting'}`}>{item.status === 'completed' ? '已完成' : '待检查'}</span>{item.is_abnormal && <span className="badge abnormal">异常</span>}</h3><p className="muted">{item.department_name}{item.category ? ` · 分类：${item.category}` : ''}{` · 结果类型：${resultTypeNames[resultType]}`}{item.unit ? ` · 单位：${item.unit}` : ''}{item.reference_range ? ` · 参考范围：${item.reference_range}` : ''}</p></div>{historyAllowed && <button className="text-button" onClick={() => setHistory(true)}>修改记录</button>}</div>{editable ? <form onSubmit={save}><div className="result-form"><label>检查结果 *<ResultValueControl type={resultType} disabled={busy} value={value} onChange={next => { setValue(next); setMessage(''); }} /></label><label>备注<textarea disabled={busy} rows={2} maxLength={5000} value={note} onChange={e => { setNote(e.target.value); setMessage(''); }} /></label></div><div className="result-actions"><AbnormalControl allowed={abnormalAllowed} disabled={busy} value={abnormal} onChange={next => { setAbnormal(next); setMessage(''); }} /><div>{dirty && <span className="unsaved-label">尚未保存</span>}<button className="primary" disabled={busy || conflict || !validResultValue(value, resultType) || (!dirty && item.status === 'completed')}>{busy ? '正在保存…' : '保存结果'}</button></div></div>{serverChanged && <div className="server-value"><strong>服务器当前结果（版本 {item.version}）：</strong><span className="pre-wrap">{item.value}</span>{item.note && <span className="pre-wrap">备注：{item.note}</span>}<span>{item.is_abnormal ? '异常' : '未标记异常'}</span></div>}<ErrorBox message={error} />{conflict && <button type="button" onClick={() => void onReload()}>刷新服务器数据，保留我的输入</button>}{message && <div className="inline-success" role="status">{message}</div>}</form> : historyAllowed ? <div className="result-value"><p className="pre-wrap">{item.value || '尚未录入'}</p>{item.note && <p className="muted pre-wrap">备注：{item.note}</p>}</div> : <p className="muted">{item.status === 'completed' ? '本科室已提交结果' : '等待科室检查'}</p>}{history && <ResultHistory item={item} onClose={() => setHistory(false)} />}</article>;
}

export default function ExamDetail({ id, user, onBack, onReport }: { id: number; user: User; onBack: () => void; onReport: () => void }) {
  const [detail, setDetail] = useState<Detail | null>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const [conclusion, setConclusion] = useState(''); const [finalizing, setFinalizing] = useState(false); const [finalError, setFinalError] = useState(''); const [message, setMessage] = useState('');
  const [dirtyItems, setDirtyItems] = useState<Record<number, boolean>>({}); const initialized = useRef(false); const loadGeneration = useRef(0);
  const [reopening, setReopening] = useState(false); const [reopenReason, setReopenReason] = useState(''); const [reopenVisible, setReopenVisible] = useState(false);
  const reviewer = user.role === 'admin' || user.role === 'reviewer';
  const conclusionDirty = reviewer && detail?.status !== 'completed' && conclusion !== (detail?.conclusion || '');
  const resultDirty = Object.values(dirtyItems).some(Boolean);
  useUnsaved(resultDirty || Boolean(conclusionDirty) || Boolean(reopenReason));
  const onDirty = useCallback((itemId: number, dirty: boolean) => setDirtyItems(previous => previous[itemId] === dirty ? previous : { ...previous, [itemId]: dirty }), []);
  async function load() {
    const generation = ++loadGeneration.current;
    setBusy(true); setError('');
    try {
      const value = await api<Detail>(`/exams/${id}`);
      if (generation !== loadGeneration.current) return;
      setDetail(value);
      if (!initialized.current) { setConclusion(value.conclusion || ''); initialized.current = true; }
    } catch (err) { if (generation === loadGeneration.current) setError(errorMessage(err)); } finally { if (generation === loadGeneration.current) setBusy(false); }
  }
  useEffect(() => { void load(); return () => { loadGeneration.current++; }; }, [id]);
  function resultSaved(item: ExamItem) {
    setDetail(previous => previous ? { ...previous, items: previous.items.map(entry => entry.id === item.id ? item : entry) } : previous);
    // Refresh overall progress and version after the committed write.
    void load();
  }
  async function finalize(event: FormEvent) {
    event.preventDefault(); if (!detail) return;
    if (!window.confirm('确认提交总检结论并生成报告？提交后检查结果将锁定，修改需要重新打开体检。')) return;
    setFinalizing(true); setFinalError(''); setMessage('');
    try { const updated = await api<Detail>(`/exams/${id}/finalize`, { method: 'POST', body: { conclusion: conclusion.trim(), version: detail.version } }); setDetail(updated); setConclusion(updated.conclusion || ''); setMessage('总检完成，体检报告已生成。'); }
    catch (err) { setFinalError(errorMessage(err)); } finally { setFinalizing(false); }
  }
  async function reopen(event: FormEvent) {
    event.preventDefault(); if (!detail) return; setReopening(true); setFinalError(''); setMessage('');
    try { const updated = await api<Detail>(`/exams/${id}/reopen`, { method: 'POST', body: { reason: reopenReason.trim(), version: detail.version } }); setDetail(updated); setConclusion(''); setReopenReason(''); setReopenVisible(false); setMessage('体检已重新打开，原报告版本已保留。检查结果修改后需重新总检。'); }
    catch (err) { setFinalError(errorMessage(err)); } finally { setReopening(false); }
  }
  if (!detail) return <><button className="text-button back-button" onClick={onBack}>← 返回工作台</button><ErrorBox message={error} />{busy ? <Loading /> : <button onClick={() => void load()}>重新加载</button>}</>;
  const canFinalize = detail.items.length > 0 && detail.items.every(item => item.status === 'completed');
  const abnormalCount = detail.items.filter(item => item.is_abnormal).length;
  return <><div className="page-heading"><div><button className="text-button back-button" onClick={onBack}>← 返回工作台</button><h1>{detail.patient.name}<Status value={detail.status} /></h1><p className="muted number">{detail.exam_number} · {detail.package_name}</p></div><button disabled={busy} onClick={() => void load()}>{busy ? '刷新中…' : '刷新数据'}</button></div><ErrorBox message={error} />{message && <div className="notice success" role="status">{message}</div>}<section className="card patient-info"><div><span>受检者</span><strong>{detail.patient.name}</strong></div><div><span>性别 / 出生日期</span><strong>{sexName(detail.patient.sex)} / {detail.patient.birth_date || '未填写'}</strong></div><div><span>联系电话</span><strong>{detail.patient.phone || '未填写'}</strong></div><div><span>{user.role === 'doctor' ? '本科室进度' : '检查进度'}</span><strong>{detail.completed_count} / {detail.total_count} 项已完成</strong></div><div><span>体检日期</span><strong>{detail.exam_date || '—'}</strong></div><div><span>体检类型</span><strong>{detail.exam_type || '个人体检'}</strong></div><div><span>登记时间</span><strong>{dateTime(detail.created_at)}</strong></div></section>{(user.role === 'admin' || user.role === 'registrar') && detail.note && <section className="card"><h2>登记备注</h2><p className="pre-wrap">{detail.note}</p></section>}<section className="card results-card"><div className="section-header"><h2>{user.role === 'doctor' ? '本科室检查项目' : '检查项目与结果'}</h2>{user.role !== 'registrar' && <span className={abnormalCount ? 'danger-text' : 'muted'}>{abnormalCount ? `${abnormalCount} 项异常` : '当前无异常标记'}</span>}</div>{detail.status === 'completed' && <div className="notice">本次体检已经总检，检查结果已锁定。需要修订时，由总检医生重新打开。</div>}{detail.items.length === 0 && <div className="notice">本次体检已登记为待检，尚未选择套餐，暂无检查项目。</div>}{detail.items.map(item => <ResultRow key={item.id} item={item} editable={detail.status !== 'completed' && (user.role === 'admin' || (user.role === 'doctor' && user.department_id === item.department_id))} historyAllowed={user.role !== 'registrar'} onSaved={resultSaved} onDirty={onDirty} onReload={load} />)}</section>{reviewer && <section className="card conclusion-card"><div className="section-header"><h2>总检结论</h2><span className="muted">报告版本 {detail.latest_report_revision || '尚未生成'}</span></div>{detail.status === 'completed' ? <><p className="pre-wrap conclusion-text">{detail.conclusion}</p><p className="muted">总检时间：{dateTime(detail.completed_at)}</p><div className="form-actions"><button onClick={() => { setReopenVisible(true); setFinalError(''); }}>重新打开体检</button><button className="primary" onClick={onReport}>查看体检报告</button></div>{reopenVisible && <form onSubmit={reopen} className="reopen-form"><label>修订原因 *<textarea disabled={reopening} required minLength={2} maxLength={1000} rows={3} placeholder="请说明重新打开体检的原因（至少2个字）" value={reopenReason} onChange={e => setReopenReason(e.target.value)} /></label><p className="field-hint">原报告会保留，当前报告暂停查看。完成修订并再次总检后，将生成新版本报告。</p><div className="form-actions"><button type="button" disabled={reopening} onClick={() => { setReopenVisible(false); setReopenReason(''); }}>取消</button><button className="primary" disabled={reopening || reopenReason.trim().length < 2}>{reopening ? '正在处理…' : '确认重新打开'}</button></div></form>}</> : <form onSubmit={finalize}>{!canFinalize && <div className="notice">{detail.items.length === 0 ? '本次体检暂无检查项目，不能进行总检。' : '还有未完成的检查项目，请待各科室全部提交后再总检。'}</div>}<label>总检结论与建议 *<textarea disabled={finalizing} required maxLength={10000} rows={6} placeholder="请综合各科室结果填写总检结论和建议" value={conclusion} onChange={e => { setConclusion(e.target.value); setMessage(''); }} /></label><div className="form-actions"><span className="field-hint">提交后生成报告并锁定检查结果。</span><button className="primary" disabled={finalizing || busy || resultDirty || !canFinalize || !conclusion.trim()}>{finalizing ? '正在提交…' : '完成总检并生成报告'}</button></div>{resultDirty && <p className="danger-text">请先保存上方尚未提交的检查结果。</p>}</form>}<ErrorBox message={finalError} />{finalError && <button onClick={() => void load()}>刷新服务器数据，保留我的输入</button>}</section>}</>;
}

