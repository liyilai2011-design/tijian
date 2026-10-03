import { ApiError } from '../api';

export type ExamRegistrationDraft = { packageId: string; packageVersion: number | null; examDate: string; examType: string; note: string };

export const blankExamRegistration = (today = ''): ExamRegistrationDraft => ({ packageId: '', packageVersion: null, examDate: today, examType: '个人体检', note: '' });
export function examRegistrationPayload(draft: ExamRegistrationDraft) {
  return { package_id: draft.packageId ? Number(draft.packageId) : null, package_version: draft.packageId ? draft.packageVersion : null, exam_date: draft.examDate || null, exam_type: draft.examType, note: draft.note.trim() || null };
}
export function packageSelectionReady(draft: ExamRegistrationDraft): boolean {
  return !draft.packageId || (Number.isSafeInteger(Number(draft.packageId)) && Number(draft.packageId) > 0 && Number.isSafeInteger(draft.packageVersion) && (draft.packageVersion ?? 0) > 0);
}
export function canRegisterWithPackage(draft: ExamRegistrationDraft, state: { confirming: boolean; hasSubmission: boolean; packageConflict: boolean }): boolean {
  return state.confirming ? state.hasSubmission : !state.packageConflict && packageSelectionReady(draft);
}
export function recoverPackageVersionConflict(draft: ExamRegistrationDraft, error: unknown): ExamRegistrationDraft | null {
  // This specific conflict follows idempotent replay and proves the old version cannot create a new exam.
  if (!draft.packageId || !(error instanceof ApiError) || error.status !== 409 || !error.message.startsWith('套餐已被修改，请重新读取套餐后核对')) return null;
  return { ...draft, packageVersion: null };
}
export function confirmedExamIdentity(value: unknown): value is { id: number; exam_number: string } {
  if (!value || typeof value !== 'object') return false;
  const exam = value as { id?: unknown; exam_number?: unknown };
  return typeof exam.id === 'number' && Number.isSafeInteger(exam.id) && exam.id > 0
    && typeof exam.exam_number === 'string' && exam.exam_number.trim().length > 0;
}
export function ExamRegistrationFields({ draft, onChange, disabled, examTypes = ['个人体检', '单位体检', '入职体检', '其他'] }: {
  draft: ExamRegistrationDraft; onChange: (draft: ExamRegistrationDraft) => void; disabled: boolean; examTypes?: string[];
}) {
  return <>
    <label>体检日期<input disabled={disabled} type="date" min="1900-01-01" value={draft.examDate}
      onChange={event => onChange({ ...draft, examDate: event.target.value })} /></label>
    <p className="field-hint">留空使用服务器当天日期。登记后可在详情中核对。</p>
    <label>体检类型<select disabled={disabled} value={draft.examType} onChange={event => onChange({ ...draft, examType: event.target.value })}>{examTypes.map(type => <option key={type} value={type}>{type}</option>)}</select></label>
    <label>本次体检备注<textarea disabled={disabled} autoComplete="off" rows={2} maxLength={2000} value={draft.note}
      onChange={event => onChange({ ...draft, note: event.target.value })} /></label>
    <p className="field-hint">选填，仅管理员和登记员查看，请填写本次登记需要说明的事项。</p>
  </>;
}
