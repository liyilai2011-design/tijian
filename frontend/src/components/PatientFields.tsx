import type { Patient, PatientRecord } from '../types';

export type PatientDraft = {
  name: string; sex: Patient['sex']; birth_date: string; phone: string;
  identity_number: string; ethnicity: string; address: string; employer: string; note: string;
};
export const blankPatient = (): PatientDraft => ({ name: '', sex: 'male', birth_date: '', phone: '', identity_number: '', ethnicity: '', address: '', employer: '', note: '' });
export const patientDraft = (patient: PatientRecord): PatientDraft => ({
  name: patient.name, sex: patient.sex, birth_date: patient.birth_date || '', phone: patient.phone || '',
  identity_number: patient.identity_number || '', employer: patient.employer || '', note: patient.note || '',
  ethnicity: patient.ethnicity || '', address: patient.address || '',
});
export function patientPayload(draft: PatientDraft) {
  return { name: draft.name.trim(), sex: draft.sex, birth_date: draft.birth_date || null, phone: draft.phone.trim() || null,
    identity_number: draft.identity_number.trim().toUpperCase() || null, ethnicity: draft.ethnicity.trim() || null, address: draft.address.trim() || null,
    employer: draft.employer.trim() || null, note: draft.note.trim() || null };
}
function todayDate() {
  const value = new Date();
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
}
export function previewAge(birthDate: string, today: string): number | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(birthDate) || birthDate < '1900-01-01' || birthDate > today) return null;
  const [year, month, day] = birthDate.split('-').map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCFullYear() !== year || date.getUTCMonth() + 1 !== month || date.getUTCDate() !== day) return null;
  return Number(today.slice(0, 4)) - year - (today.slice(5) < birthDate.slice(5) ? 1 : 0);
}
export function PatientFields({ draft, onChange, disabled, today = todayDate(), onIdentityBlur, compact = false }: { draft: PatientDraft; onChange: (draft: PatientDraft) => void; disabled: boolean; today?: string; onIdentityBlur?: () => void; compact?: boolean }) {
  const age = previewAge(draft.birth_date, today);
  function change(key: keyof PatientDraft, value: string) { onChange({ ...draft, [key]: value }); }
  const extraFields = <>
    <label>联系电话<input disabled={disabled} type="tel" maxLength={32} autoComplete="off" value={draft.phone} onChange={event => change('phone', event.target.value)} /></label>
    <label>单位<input disabled={disabled} maxLength={200} autoComplete="off" value={draft.employer} onChange={event => change('employer', event.target.value)} /></label>
    <label>人员备注<textarea disabled={disabled} autoComplete="off" rows={2} maxLength={2000} value={draft.note} onChange={event => change('note', event.target.value)} /></label>
  </>;
  return <>
    <label>姓名 *<input disabled={disabled} required maxLength={100} autoComplete="off" value={draft.name} onChange={event => change('name', event.target.value)} /></label>
    <div className="field-grid"><label>性别 *<select disabled={disabled} value={draft.sex} onChange={event => change('sex', event.target.value)}><option value="male">男</option><option value="female">女</option><option value="other">其他</option></select></label>
      <label>出生日期<input disabled={disabled} type="date" min="1900-01-01" max={today} value={draft.birth_date} onChange={event => change('birth_date', event.target.value)} /></label></div>
    <p className="field-hint">年龄（自动计算）：{age === null ? '未填写有效出生日期' : `${age} 岁`}。保存后以服务器日期计算。</p>
    <label>身份证号<input disabled={disabled} maxLength={18} autoComplete="off" pattern="([0-9]{15}|[0-9]{17}[0-9Xx])" value={draft.identity_number} onBlur={onIdentityBlur} onChange={event => change('identity_number', event.target.value)} /><span className="field-hint">选填，15 位数字或 18 位号码，末位可为 X。</span></label>
    <label>民族<input disabled={disabled} maxLength={50} autoComplete="off" value={draft.ethnicity} onChange={event => change('ethnicity', event.target.value)} /></label>
    <label>地址<input disabled={disabled} maxLength={500} autoComplete="off" value={draft.address} onChange={event => change('address', event.target.value)} /></label>
    {compact ? <details className="optional-person-fields"><summary>其他人员资料（选填）</summary>{extraFields}</details> : extraFields}
  </>;
}
