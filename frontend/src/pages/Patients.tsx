import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { api, ApiError, errorMessage, requestId } from '../api';
import type { ExamDetail, Page, Patient, PatientRecord, RegistrationOptions, User } from '../types';
import { ErrorBox, Pagination, sexName } from '../ui';
import { ExamRegistrationFields, blankExamRegistration, canRegisterWithPackage, examRegistrationPayload, confirmedExamIdentity, recoverPackageVersionConflict } from '../components/ExamRegistrationFields';
import { RegistrationForm } from '../components/RegistrationForm';
import { PackagePicker } from '../components/PackagePicker';
import { useUnsaved } from '../useUnsaved';

function CreateExam({ patient, onClose, onCreated }: { patient: Patient; onClose: () => void; onCreated: (id: number) => void }) {
  const [draft, setDraft] = useState(blankExamRegistration); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [options, setOptions] = useState<RegistrationOptions | null>(null); const [optionsError, setOptionsError] = useState(''); const [optionReload, setOptionReload] = useState(0);
  const submission = useRef<{ fingerprint: string; id: string; fields: ReturnType<typeof examRegistrationPayload> } | null>(null);
  const [packageConflict, setPackageConflict] = useState(false); const [packageReload, setPackageReload] = useState(0);
  useEffect(() => { let active = true; setOptionsError(''); api<RegistrationOptions>('/registration/options').then(value => { if (active) { setOptions(value); setDraft(previous => ({ ...previous, examDate: previous.examDate || value.today })); } }).catch(failure => { if (active) setOptionsError(errorMessage(failure)); }); return () => { active = false; }; }, [optionReload]);
  useUnsaved(true, confirming || busy);
  async function create(event: FormEvent) {
    event.preventDefault(); if (busy || !canRegisterWithPackage(draft, { confirming, hasSubmission: submission.current !== null, packageConflict })) return;
    if (confirming && !submission.current) { setError('原登记请求暂时无法核对，请保持当前页面，不要重复登记。'); return; }
    setError(''); setBusy(true);
    const fields = confirming && submission.current ? submission.current.fields : examRegistrationPayload(draft);
    const fingerprint = JSON.stringify(fields);
    if (!submission.current || submission.current.fingerprint !== fingerprint) submission.current = { fingerprint, id: requestId(), fields };
    try {
      const exam = await api<ExamDetail>('/exams', { method: 'POST', body: { patient_id: patient.id, ...fields, request_id: submission.current.id } });
      if (!confirmedExamIdentity(exam)) throw new ApiError('登记结果尚未确认，请重试核对，避免重复登记。', 200, true);
      onCreated(exam.id);
    } catch (err) {
      setError(errorMessage(err)); if (err instanceof ApiError && err.uncertain) setConfirming(true);
      const recoveredDraft = recoverPackageVersionConflict(draft, err);
      if (recoveredDraft) { setDraft(recoveredDraft); setConfirming(false); submission.current = null; setPackageConflict(true); }
      else if (err instanceof ApiError && err.status === 409 && !confirming && draft.packageId && err.message.includes('套餐')) setPackageConflict(true);
    }
    finally { setBusy(false); }
  }
  const locked = busy || confirming;
  return <div className="modal-overlay"><section className="modal" role="dialog" aria-modal="true" aria-labelledby="create-title">
    <div className="section-header"><div><p className="eyebrow">创建体检</p><h2 id="create-title">{patient.name}</h2></div><button type="button" disabled={locked} onClick={onClose}>关闭</button></div>
    <p className="muted">{sexName(patient.sex)} · {patient.birth_date || '出生日期未填'} · {patient.phone || '电话未填'}</p>
    <form onSubmit={create}>
      <ExamRegistrationFields draft={draft} onChange={setDraft} disabled={locked} examTypes={options?.exam_types} />
      <PackagePicker value={draft.packageId} version={draft.packageVersion} disabled={locked} reloadKey={packageReload} onChange={(id, version) => { setDraft(previous => ({ ...previous, packageId: id, packageVersion: version })); setPackageConflict(false); }} />
      {!draft.packageId && <div className="notice">可先登记本次体检。未选套餐时暂无检查项目，状态为待检。</div>}<ErrorBox message={error} /><ErrorBox message={optionsError} />{optionsError && <button type="button" disabled={locked} onClick={() => setOptionReload(value => value + 1)}>重读登记选项</button>}
      {confirming && <div className="notice" role="status">登记结果尚未确认。请保持当前窗口，恢复连接后重试核对；核对前保留原资料，避免重复登记。</div>}
      {packageConflict && <div className="notice" role="status"><p>套餐已发生变化，本次输入已保留。请重新读取套餐项目并核对后再登记。</p><button type="button" disabled={locked} onClick={() => setPackageReload(value => value + 1)}>重新读取套餐并核对</button></div>}
      <div className="form-actions"><button type="button" disabled={locked} onClick={onClose}>取消</button><button className="primary" disabled={busy || !options || !canRegisterWithPackage(draft, { confirming, hasSubmission: submission.current !== null, packageConflict })}>{busy ? '正在核对并保存…' : confirming ? '重试并核对登记' : '创建体检并查看'}</button></div>
    </form>
  </section></div>;
}

function ArchivePatient({ patient, onClose, onSaved }: { patient: PatientRecord; onClose: () => void; onSaved: () => void }) {
  const [reason, setReason] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  async function archive(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try { await api(`/patients/${patient.id}/archive`, { method: 'POST', body: { reason: reason.trim(), version: patient.version } }); onSaved(); }
    catch (failure) { setError(errorMessage(failure)); }
    finally { setBusy(false); }
  }
  return <div className="modal-overlay"><section className="modal" role="dialog" aria-modal="true" aria-labelledby="archive-title"><h2 id="archive-title">归档受检者</h2><p>归档「{patient.name}」后，档案从登记列表隐藏，数据和操作记录仍保留。有体检记录的档案不能归档。</p><form onSubmit={archive}><label>归档原因 *<textarea disabled={busy} required minLength={2} maxLength={1000} rows={3} value={reason} onChange={event => setReason(event.target.value)} /></label><ErrorBox message={error} /><div className="form-actions"><button type="button" disabled={busy} onClick={onClose}>取消归档</button><button className="danger" disabled={busy || reason.trim().length < 2}>{busy ? '正在归档…' : '确认归档'}</button></div></form></section></div>;
}

export default function Patients({ user, onExam, onPatient }: { user: User; onExam: (id: number) => void; onPatient: (id: number) => void }) {
  const [patients, setPatients] = useState<Page<PatientRecord>>({ items: [], total: 0, page: 1, page_size: 20 });
  const [page, setPage] = useState(1); const [queryInput, setQueryInput] = useState(''); const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [refresh, setRefresh] = useState(0);
  const [saving, setSaving] = useState(false); const [message, setMessage] = useState('');
  const [examPatient, setExamPatient] = useState<Patient | null>(null);
  const [archivePatient, setArchivePatient] = useState<PatientRecord | null>(null);
  const [createdExamId, setCreatedExamId] = useState<number | null>(null);
  useUnsaved(Boolean(archivePatient));
  useEffect(() => { if (createdExamId !== null) { onExam(createdExamId); setCreatedExamId(null); } }, [createdExamId]);
  useEffect(() => {
    let active = true; setBusy(true); setError('');
    api<Page<PatientRecord>>(`/patients?page=${page}&page_size=20&q=${encodeURIComponent(query)}`)
      .then(data => { if (active) setPatients(data); }).catch(err => { if (active) setError(errorMessage(err)); }).finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [page, query, refresh]);
  return <>
    <div className="page-heading"><div><p className="eyebrow">前台工作</p><h1>受检者登记</h1><p className="muted">读卡或手工填写人员资料，确认登记本次体检；已有档案可查看历史或继续创建体检。</p></div></div>
    <div className="registration-layout">
      <RegistrationForm onBusyChange={setSaving} onRegistered={() => { setPage(1); setQuery(''); setQueryInput(''); setRefresh(value => value + 1); setMessage('人员及本次体检已由服务器确认保存。'); }} onExam={onExam} onPatient={onPatient} />
      <section className="card list-card"><div className="section-header"><h2>受检者档案</h2><button disabled={busy || saving} onClick={() => setRefresh(n => n + 1)}>刷新</button></div>
        <form className="search-bar" onSubmit={event => { event.preventDefault(); setPage(1); setQuery(queryInput.trim()); setRefresh(n => n + 1); }}><input aria-label="搜索受检者" placeholder="输入姓名、电话或身份证号" maxLength={100} value={queryInput} onChange={event => setQueryInput(event.target.value)} /><button className="primary" disabled={busy}>查询</button></form>
        {message && <div className="notice success" role="status">{message}</div>}<ErrorBox message={error} />
        <div className="table-scroll"><table aria-label="受检者档案列表" aria-busy={busy}><thead><tr><th>姓名 / 单位</th><th>性别 / 年龄</th><th>出生日期</th><th>联系电话</th><th className="align-right">操作</th></tr></thead><tbody>
          {patients.items.map(patient => <tr key={patient.id}><td><strong>{patient.name}</strong>{patient.employer && <small className="cell-sub">{patient.employer}</small>}</td><td>{sexName(patient.sex)}{patient.age !== null && <small className="cell-sub">{patient.age} 岁</small>}</td><td>{patient.birth_date || '—'}</td><td>{patient.phone || '—'}</td><td className="align-right actions">
            <button className="link-button" disabled={saving} aria-label={`查看档案 ${patient.name}`} onClick={() => onPatient(patient.id)}>查看档案</button>
            <button className="link-button" disabled={saving} onClick={() => { setMessage(''); setExamPatient(patient); }}>创建体检</button>
            {user.role === 'admin' && <button className="link-button danger-text" disabled={saving} aria-label={`归档 ${patient.name}`} onClick={() => setArchivePatient(patient)}>归档</button>}
          </td></tr>)}
          {patients.items.length === 0 && <tr><td className="empty" colSpan={5}>{busy ? '正在加载受检者…' : error ? '数据加载失败，请重试' : '暂无受检者，请先登记或调整查询条件'}</td></tr>}
        </tbody></table></div><Pagination page={page} total={patients.total} busy={busy} onPage={setPage} />
      </section>
    </div>
    {examPatient && <CreateExam patient={examPatient} onClose={() => setExamPatient(null)} onCreated={id => { setExamPatient(null); setMessage('体检已创建成功。'); setCreatedExamId(id); }} />}
    {archivePatient && <ArchivePatient patient={archivePatient} onClose={() => setArchivePatient(null)} onSaved={() => { setArchivePatient(null); setRefresh(value => value + 1); setMessage('受检者已归档，数据和历史操作记录仍保留。'); }} />}
  </>;
}

