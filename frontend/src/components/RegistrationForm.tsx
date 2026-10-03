import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { api, ApiError, errorMessage, requestId } from '../api';
import { identityReader, simulatedIdentityReader, type IdentityReader } from '../identity-reader';
import type { Exam, Page, PatientRecord, RegistrationOptions, RegistrationResult } from '../types';
import { ErrorBox, sexName } from '../ui';
import { useUnsaved } from '../useUnsaved';
import { blankPatient, PatientFields, patientDraft, patientPayload, type PatientDraft } from './PatientFields';
import { blankExamRegistration, canRegisterWithPackage, confirmedExamIdentity, ExamRegistrationFields, examRegistrationPayload, recoverPackageVersionConflict, type ExamRegistrationDraft } from './ExamRegistrationFields';
import { PackagePicker } from './PackagePicker';

export function registrationPayload(patient: PatientDraft, exam: ExamRegistrationDraft, selected: PatientRecord | null) {
  return { patient: patientPayload(patient), patient_id: selected?.id ?? null, patient_version: selected?.version ?? null, ...examRegistrationPayload(exam) };
}
export function confirmedRegistration(value: unknown): value is RegistrationResult {
  if (!value || typeof value !== 'object') return false;
  const result = value as Partial<RegistrationResult>;
  return Boolean(result.patient && Number.isSafeInteger(result.patient.id) && result.patient.id > 0
    && Number.isSafeInteger(result.patient.version) && result.patient.version > 0 && confirmedExamIdentity(result.exam)
    && result.exam.patient && result.exam.patient.id === result.patient.id && typeof result.reused_patient === 'boolean');
}
export function identityDifferences(source: PatientDraft, patient: PatientRecord): string[] {
  const differences: string[] = [];
  if (source.name.trim() && source.name.trim() !== patient.name) differences.push('姓名');
  if (source.name.trim() && source.sex !== patient.sex) differences.push('性别');
  if (source.birth_date && source.birth_date !== patient.birth_date) differences.push('出生日期');
  return differences;
}
const normalizedIdentity = (value: string) => value.trim().toUpperCase();

export function RegistrationForm({ onRegistered, onExam, onPatient, onBusyChange }: {
  onRegistered: () => void; onExam: (id: number) => void; onPatient: (id: number) => void; onBusyChange: (busy: boolean) => void;
}) {
  const [draft, setDraft] = useState(blankPatient); const [exam, setExam] = useState(blankExamRegistration);
  const [options, setOptions] = useState<RegistrationOptions | null>(null); const [optionError, setOptionError] = useState(''); const [optionsReload, setOptionsReload] = useState(0);
  const [selected, setSelected] = useState<PatientRecord | null>(null); const [historyCount, setHistoryCount] = useState<number | null>(null);
  const [lookupMessage, setLookupMessage] = useState(''); const [error, setError] = useState(''); const [simulated, setSimulated] = useState(false);
  const [reading, setReading] = useState(false); const [saving, setSaving] = useState(false); const [confirming, setConfirming] = useState(false);
  const [saved, setSaved] = useState<RegistrationResult | null>(null);
  const [patientConflict, setPatientConflict] = useState(false);
  const [packageConflict, setPackageConflict] = useState(false); const [packageReload, setPackageReload] = useState(0);
  const generation = useRef(0); const currentIdentity = useRef(''); const checkedIdentity = useRef(''); const submitting = useRef(false);
  const submission = useRef<{ fingerprint: string; requestId: string; fields: ReturnType<typeof registrationPayload> } | null>(null);
  const busy = reading || saving; const locked = busy || confirming; const controlsLocked = locked || Boolean(saved);
  const dirty = !saved && (Boolean(selected) || JSON.stringify(draft) !== JSON.stringify(blankPatient())
    || exam.packageId !== '' || exam.note !== '' || exam.examType !== '个人体检' || Boolean(options && exam.examDate !== options.today));
  useUnsaved(dirty || confirming, confirming || saving);
  useEffect(() => { onBusyChange(locked); return () => onBusyChange(false); }, [locked, onBusyChange]);
  useEffect(() => {
    let active = true; setOptionError('');
    api<RegistrationOptions>('/registration/options').then(value => {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(value.today) || !Array.isArray(value.exam_types) || value.exam_types.length === 0) throw new Error('登记选项暂时无法读取，请重试。');
      if (active) { setOptions(value); setExam(previous => ({ ...previous, examDate: previous.examDate || value.today, examType: value.exam_types.includes(previous.examType) ? previous.examType : value.exam_types[0] })); }
    }).catch(failure => { if (active) setOptionError(errorMessage(failure)); });
    return () => { active = false; };
  }, [optionsReload]);
  useEffect(() => () => { generation.current++; }, []);

  function changePatient(value: PatientDraft) {
    if (controlsLocked) return;
    const identity = normalizedIdentity(value.identity_number);
    if (identity !== currentIdentity.current) { generation.current++; checkedIdentity.current = ''; setSelected(null); setHistoryCount(null); setLookupMessage(''); }
    currentIdentity.current = identity; setDraft(value); setError('');
  }
  async function lookup(identity: string, source = draft): Promise<PatientRecord | null | undefined> {
    const normalized = normalizedIdentity(identity); if (!normalized) return null;
    if (!/^([0-9]{15}|[0-9]{17}[0-9X])$/.test(normalized)) { setError('请先填写完整的 15 位或 18 位身份证号。'); return undefined; }
    const requestGeneration = ++generation.current; setError('');
    try {
      const result = await api<{ patient: PatientRecord | null }>('/patients/lookup-identity', { method: 'POST', body: { identity_number: normalized } });
      if (requestGeneration !== generation.current || currentIdentity.current !== normalized) return undefined;
      if (!result || !Object.prototype.hasOwnProperty.call(result, 'patient')) throw new Error('档案查询结果暂时无法确认，请重试查询。');
      checkedIdentity.current = normalized;
      setPatientConflict(false);
      if (result.patient) {
        const patient = result.patient; setSelected(patient); setDraft(patientDraft(patient)); setHistoryCount(null);
        const differences = identityDifferences(source, patient);
        setLookupMessage(differences.length ? `读卡或当前填写资料与已有档案的${differences.join('、')}不同，请核对。本次使用服务器已有档案，不覆盖原资料。` : '已找到相同身份证号的已有档案。本次使用服务器资料，不覆盖原档案；请核对后确认登记。');
        void api<Page<Exam>>(`/patients/${patient.id}/exams?page=1&page_size=1`).then(history => {
          if (requestGeneration === generation.current && currentIdentity.current === normalized) setHistoryCount(history.total);
        }).catch(() => { /* The separate history page remains available if this optional count fails. */ });
        return patient;
      }
      setSelected(null); setHistoryCount(null); setLookupMessage('未找到相同身份证号的档案，将登记为新受检者。'); return null;
    } catch (failure) { if (requestGeneration === generation.current) setError(errorMessage(failure)); return undefined; }
  }
  async function findIdentity() {
    if (controlsLocked || !draft.identity_number.trim()) return;
    setReading(true); try { await lookup(draft.identity_number); } finally { setReading(false); }
  }
  async function readCard(reader: IdentityReader, mock: boolean) {
    if (controlsLocked) return; setReading(true); setError('');
    try {
      const card = await reader.read(); const value = { ...blankPatient(), ...card };
      currentIdentity.current = normalizedIdentity(card.identity_number); checkedIdentity.current = ''; generation.current++;
      setDraft(value); setSelected(null); setHistoryCount(null); setSimulated(mock); setLookupMessage('');
      await lookup(card.identity_number, value);
    } catch (failure) { setError(errorMessage(failure)); } finally { setReading(false); }
  }
  function reset() {
    if (locked) return; generation.current++; currentIdentity.current = ''; checkedIdentity.current = ''; submission.current = null;
    setDraft(blankPatient()); setExam(blankExamRegistration(options?.today)); setSelected(null); setHistoryCount(null);
    setLookupMessage(''); setError(''); setSimulated(false); setSaved(null); setConfirming(false); setPatientConflict(false); setPackageConflict(false);
  }
  async function register(event: FormEvent) {
    event.preventDefault(); if (submitting.current || reading || saved || !options || (!confirming && patientConflict) || !canRegisterWithPackage(exam, { confirming, hasSubmission: submission.current !== null, packageConflict })) return;
    if (confirming && !submission.current) { setError('原登记请求暂时无法核对，请保持当前页面，不要重复登记。'); return; }
    submitting.current = true; setError('');
    try {
      if (!confirming && draft.identity_number.trim() && checkedIdentity.current !== normalizedIdentity(draft.identity_number)) {
        setReading(true); const match = await lookup(draft.identity_number); setReading(false);
        if (match === undefined || match) return;
      }
      setSaving(true);
      const fields = confirming && submission.current ? submission.current.fields : registrationPayload(draft, exam, selected);
      const fingerprint = JSON.stringify(fields);
      if (!submission.current || submission.current.fingerprint !== fingerprint) submission.current = { fingerprint, fields, requestId: requestId() };
      const result = await api<RegistrationResult>('/registrations', { method: 'POST', body: { ...fields, request_id: submission.current.requestId } });
      if (!confirmedRegistration(result)) throw new ApiError('登记结果尚未确认，请保持当前资料并重试核对。', 200, true);
      setSaved(result); setConfirming(false); submission.current = null; onRegistered();
    } catch (failure) {
      setError(errorMessage(failure)); if (failure instanceof ApiError && failure.uncertain) setConfirming(true);
      const recoveredExam = recoverPackageVersionConflict(exam, failure);
      if (recoveredExam) {
        setExam(recoveredExam); setConfirming(false); submission.current = null; setPackageConflict(true);
      } else if (failure instanceof ApiError && failure.status === 409 && !confirming) {
        if (exam.packageId && failure.message.includes('套餐')) setPackageConflict(true);
        else if (selected) setPatientConflict(true);
      }
    } finally { submitting.current = false; setSaving(false); setReading(false); }
  }
  return <section className="card register-card combined-register-card">
    <div className="section-header"><h2>人员及本次体检</h2><span className="muted">必填项 *</span></div>
    <form onSubmit={register} autoComplete="off">
      <div className="registration-part"><h3>人员资料</h3><div className="reader-actions"><button type="button" disabled={controlsLocked} onClick={() => void readCard(identityReader, false)}>读取身份证</button><button type="button" disabled={controlsLocked} onClick={() => void readCard(simulatedIdentityReader, true)}>模拟读取身份证</button></div>
        <p className="field-hint">读卡器尚未接入，可手工填写。模拟读取仅用于软件测试，使用明确的虚构资料，不能读取实体身份证。</p>
        {simulated && <div className="notice" role="status">当前为模拟读卡资料（虚构），仅供软件验收。</div>}
        {selected && <div className="notice" role="status"><strong>使用已有档案：{selected.name}</strong><p>{sexName(selected.sex)} · {selected.birth_date || '出生日期未填'}{historyCount === null ? '' : ` · ${historyCount} 次历史体检`}</p><p>原资料以档案为准，修改请进入档案页面。</p><div className="reader-actions"><button type="button" disabled={locked} onClick={() => onPatient(selected.id)}>查看档案和历史</button><button type="button" disabled={locked} onClick={reset}>更换受检者</button></div></div>}
        <PatientFields draft={draft} onChange={changePatient} disabled={controlsLocked || Boolean(selected)} compact today={options?.today || ''} onIdentityBlur={() => { if (!selected && checkedIdentity.current !== normalizedIdentity(draft.identity_number)) void findIdentity(); }} />
        {!selected && <button type="button" disabled={controlsLocked || !draft.identity_number.trim()} onClick={() => void findIdentity()}>按身份证号查询已有档案</button>}
        {lookupMessage && <p className="field-hint" role="status">{lookupMessage}</p>}
        {!draft.identity_number.trim() && <p className="field-hint">无身份证号也可手工登记；请先在右侧搜索已有档案，避免重复建立同名人员。</p>}
      </div>
      <div className="registration-part"><h3>本次体检</h3><ExamRegistrationFields draft={exam} onChange={setExam} disabled={controlsLocked} examTypes={options?.exam_types} />
        <PackagePicker value={exam.packageId} version={exam.packageVersion} disabled={controlsLocked} reloadKey={packageReload} onChange={(id, version) => { setExam(previous => ({ ...previous, packageId: id, packageVersion: version })); setPackageConflict(false); }} />
      </div>
      <ErrorBox message={optionError} />{optionError && <button type="button" disabled={locked} onClick={() => setOptionsReload(value => value + 1)}>重新读取登记选项</button>}
      <ErrorBox message={error} />
      {patientConflict && <div className="notice" role="status"><p>已有档案可能已被修改，本次体检输入已保留。请重新核对服务器资料后再登记。</p><button type="button" disabled={locked} onClick={() => void findIdentity()}>重新核对已有档案（保留本次体检）</button></div>}
      {packageConflict && <div className="notice" role="status"><p>套餐已发生变化，人员资料及本次体检输入已保留。请重新读取项目并核对后再登记。</p><button type="button" disabled={locked} onClick={() => setPackageReload(value => value + 1)}>重新读取套餐并核对</button></div>}
      {confirming && <div className="notice" role="status">登记结果尚未确认。请保持当前页面，恢复连接后重试核对；原人员资料及本次体检已保留，核对前不能更换受检者。</div>}
      {saved ? <div className="notice success" role="status"><strong>登记成功，服务器已确认。</strong><p>体检编号：<strong>{saved.exam.exam_number}</strong></p><p>{saved.patient.name} · {saved.exam.exam_date} · {saved.exam.exam_type} · 待检</p><div className="reader-actions"><button type="button" className="primary" onClick={() => onExam(saved.exam.id)}>查看本次体检</button><button type="button" onClick={reset}>登记下一位</button></div></div>
        : <button className="primary full" disabled={busy || !options || (!confirming && patientConflict) || !canRegisterWithPackage(exam, { confirming, hasSubmission: submission.current !== null, packageConflict }) || !draft.name.trim()}>{saving ? '正在核对并保存…' : reading ? '正在读取并核对…' : confirming ? '重试并核对登记' : '确认登记'}</button>}
    </form>
  </section>;
}
