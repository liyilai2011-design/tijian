import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { api, ApiError, errorMessage } from '../api';
import type { Exam, Page, PatientRecord } from '../types';
import { PatientFields, patientDraft, patientPayload, type PatientDraft } from '../components/PatientFields';
import { dateTime, ErrorBox, Loading, Pagination, sexName, Status } from '../ui';
import { useUnsaved } from '../useUnsaved';
import './PatientDetail.css';

function PatientFacts({ patient }: { patient: PatientRecord }) {
  return <dl className="patient-facts"><dt>姓名</dt><dd>{patient.name}</dd><dt>性别 / 年龄</dt><dd>{sexName(patient.sex)} · {patient.age === null ? '年龄未填' : `${patient.age} 岁`}</dd>
    <dt>出生日期</dt><dd>{patient.birth_date || '—'}</dd><dt>身份证号</dt><dd>{patient.identity_number || '—'}</dd><dt>联系电话</dt><dd>{patient.phone || '—'}</dd>
    <dt>民族</dt><dd>{patient.ethnicity || '—'}</dd><dt>地址</dt><dd>{patient.address || '—'}</dd>
    <dt>单位</dt><dd>{patient.employer || '—'}</dd><dt>备注</dt><dd>{patient.note || '—'}</dd><dt>创建时间</dt><dd>{dateTime(patient.created_at)}</dd><dt>更新时间</dt><dd>{dateTime(patient.updated_at)}</dd></dl>;
}
export default function PatientDetail({ id, onBack, onExam }: { id: number; onBack: () => void; onExam: (id: number) => void }) {
  const [patient, setPatient] = useState<PatientRecord | null>(null); const [loadError, setLoadError] = useState('');
  const [loading, setLoading] = useState(true); const [reload, setReload] = useState(0);
  const [draft, setDraft] = useState<PatientDraft | null>(null); const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(''); const [message, setMessage] = useState('');
  const [conflicted, setConflicted] = useState(false); const [latest, setLatest] = useState<PatientRecord | null>(null); const [reading, setReading] = useState(false);
  const [discarding, setDiscarding] = useState(false);
  const [history, setHistory] = useState<Page<Exam>>({ items: [], total: 0, page: 1, page_size: 20 });
  const [historyPage, setHistoryPage] = useState(1); const [historyRefresh, setHistoryRefresh] = useState(0);
  const [historyBusy, setHistoryBusy] = useState(false); const [historyError, setHistoryError] = useState('');
  const dirty = Boolean(draft && patient && JSON.stringify(draft) !== JSON.stringify(patientDraft(patient)));
  useUnsaved(dirty);
  useEffect(() => {
    let active = true; setLoading(true); setLoadError('');
    api<PatientRecord>(`/patients/${id}`).then(value => { if (active) setPatient(value); })
      .catch(error => { if (active) setLoadError(errorMessage(error)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [id, reload]);
  useEffect(() => {
    let active = true; setHistoryBusy(true); setHistoryError('');
    api<Page<Exam>>(`/patients/${id}/exams?page=${historyPage}&page_size=20`).then(value => { if (active) setHistory(value); })
      .catch(error => { if (active) setHistoryError(errorMessage(error)); }).finally(() => { if (active) setHistoryBusy(false); });
    return () => { active = false; };
  }, [id, historyPage, historyRefresh]);
  function cancelEdit() {
    setDraft(null); setDiscarding(false); setSaveError(''); setConflicted(false); setLatest(null);
  }
  async function save(event: FormEvent) {
    event.preventDefault(); if (!patient || !draft) return;
    setSaving(true); setSaveError(''); setMessage('');
    try {
      const saved = await api<PatientRecord>(`/patients/${id}`, { method: 'PUT', body: { ...patientPayload(draft), version: patient.version } });
      setPatient(saved); setDraft(null); setConflicted(false); setLatest(null); setMessage('受检者资料已保存。历史体检和已出具的报告保留原资料。');
    } catch (error) {
      setSaveError(errorMessage(error));
      if (error instanceof ApiError && error.status === 409) { setConflicted(true); setLatest(null); }
    } finally { setSaving(false); }
  }
  async function readLatest() {
    setReading(true); setSaveError('');
    try { setLatest(await api<PatientRecord>(`/patients/${id}`)); }
    catch (error) { setSaveError(errorMessage(error)); }
    finally { setReading(false); }
  }
  function acceptLatest() {
    if (!latest) return;
    setPatient(latest); setLatest(null); setConflicted(false); setSaveError('');
    setMessage('已取得最新资料，您的输入仍保留。请核对后再保存。');
  }
  const busy = saving || reading;
  if (loading && !patient) return <Loading />;
  if (!patient) return <section className="card"><h1>受检者档案暂时无法加载</h1><ErrorBox message={loadError} /><div className="form-actions"><button onClick={onBack}>返回档案列表</button><button className="primary" onClick={() => setReload(value => value + 1)}>重试加载</button></div></section>;
  return <>
    <div className="page-heading"><div className="patient-detail-heading"><button disabled={busy} onClick={onBack}>← 返回</button><div><p className="eyebrow">受检者档案</p><h1>{patient.name}</h1><p className="muted">资料与历史体检由服务器统一保存。</p></div></div></div>
    <ErrorBox message={loadError} />
    {message && <div className="notice" role="status">{message}</div>}
    <div className="patient-detail-layout">
      <section className="card patient-editor"><div className="section-header"><h2>{draft ? '编辑受检者资料' : '基本资料'}</h2>{!draft && <button onClick={() => { setDraft(patientDraft(patient)); setMessage(''); }}>编辑资料</button>}</div>
        {draft ? <form onSubmit={save}>
          <PatientFields draft={draft} onChange={setDraft} disabled={busy} />
          <ErrorBox message={saveError} />
          {conflicted && <div className="patient-conflict" role="status"><p>您的输入已保留。请先读取服务器最新资料，核对其他人已保存的修改。</p><button type="button" disabled={busy} onClick={readLatest}>{reading ? '正在读取…' : '读取最新资料（保留输入）'}</button>
            {latest && <><PatientFacts patient={latest} /><button type="button" disabled={busy} onClick={acceptLatest}>已核对，保留输入继续修改</button></>}
          </div>}
          <div className="form-actions"><button type="button" disabled={busy} onClick={() => dirty ? setDiscarding(true) : cancelEdit()}>取消编辑</button><button className="primary" disabled={busy || !dirty || conflicted || !draft.name.trim()}>{saving ? '正在保存…' : '保存资料'}</button></div>
        </form> : <PatientFacts patient={patient} />}
      </section>
      <section className="card list-card history-card"><div className="section-header"><h2>历史体检记录</h2><button disabled={historyBusy || busy} onClick={() => setHistoryRefresh(value => value + 1)}>刷新历史</button></div>
        <p className="muted">按体检登记时间显示。修改当前档案不会改写以前的体检资料或报告。</p>
        <ErrorBox message={historyError} />
        <div className="table-scroll"><table aria-label="历史体检记录" aria-busy={historyBusy}><thead><tr><th>体检编号 / 体检日期</th><th>套餐</th><th>进度</th><th>状态</th><th className="align-right">操作</th></tr></thead><tbody>
          {history.items.map(exam => <tr key={exam.id}><td><strong>{exam.exam_number}</strong><small className="cell-sub">{exam.exam_date || '—'} · {exam.exam_type}</small></td><td>{exam.package_name || '未选择套餐'}</td><td>{exam.completed_count} / {exam.total_count}</td><td><Status value={exam.status} /></td><td className="align-right"><button className="link-button" disabled={busy} aria-label={`查看体检 ${exam.exam_number}`} onClick={() => onExam(exam.id)}>查看体检</button></td></tr>)}
          {history.items.length === 0 && <tr><td className="empty" colSpan={5}>{historyBusy ? '正在加载历史体检…' : historyError ? '历史加载失败，请重试' : '暂无历史体检记录'}</td></tr>}
        </tbody></table></div><Pagination page={historyPage} total={history.total} busy={historyBusy} onPage={setHistoryPage} />
      </section>
    </div>
    {discarding && <div className="modal-overlay"><section className="modal" role="dialog" aria-modal="true" aria-labelledby="discard-title"><h2 id="discard-title">放弃当前修改？</h2><p>本次输入尚未保存，已保存在服务器的资料不受影响。</p><div className="form-actions"><button onClick={() => setDiscarding(false)}>继续编辑</button><button className="danger" onClick={cancelEdit}>放弃修改</button></div></section></div>}
  </>;
}
