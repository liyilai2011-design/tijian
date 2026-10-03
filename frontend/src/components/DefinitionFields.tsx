import type { CatalogRecord, Department, DepartmentRecord, ResultType } from '../types';

export type DepartmentDraft = { name: string; is_active: boolean };
export type CatalogDraft = DepartmentDraft & { departmentId: string; category: string; unit: string; referenceRange: string; resultType: ResultType; abnormalAllowed: boolean };
export const blankDefinition = (): CatalogDraft => ({ name: '', is_active: true, departmentId: '', category: '', unit: '', referenceRange: '', resultType: 'text', abnormalAllowed: true });
export function definitionDraft(record: DepartmentRecord | CatalogRecord): CatalogDraft {
  return { ...blankDefinition(), name: record.name, is_active: record.is_active,
    ...('department_id' in record ? { departmentId: String(record.department_id), category: record.category || '', unit: record.unit || '', referenceRange: record.reference_range || '', resultType: record.result_type, abnormalAllowed: record.abnormal_allowed } : {}) };
}
export function departmentPayload(draft: DepartmentDraft) { return { name: draft.name.trim(), is_active: draft.is_active }; }
export function departmentCreatePayload(draft: DepartmentDraft) { return { name: draft.name.trim() }; }
export function catalogPayload(draft: CatalogDraft) {
  return { ...departmentPayload(draft), department_id: Number(draft.departmentId), category: draft.category.trim(), unit: draft.unit.trim(),
    reference_range: draft.referenceRange.trim(), result_type: draft.resultType, abnormal_allowed: draft.abnormalAllowed };
}
export function definitionMatches(record: DepartmentRecord | CatalogRecord, fields: Record<string, unknown>, catalog: boolean) {
  const draft = definitionDraft(record); const normalized: Record<string, unknown> = catalog ? catalogPayload(draft) : departmentPayload(draft);
  return Object.entries(fields).every(([key, value]) => normalized[key] === value);
}
export function confirmedDefinition(value: unknown, catalog: boolean): value is DepartmentRecord | CatalogRecord {
  if (!value || typeof value !== 'object') return false;
  const record = value as Partial<CatalogRecord>;
  return Boolean(Number.isSafeInteger(record.id) && (record.id ?? 0) > 0 && Number.isSafeInteger(record.version) && (record.version ?? 0) > 0
    && typeof record.name === 'string' && record.name.trim() && typeof record.is_active === 'boolean'
    && (!catalog || (Number.isSafeInteger(record.department_id) && (record.department_id ?? 0) > 0
      && typeof record.category === 'string' && typeof record.abnormal_allowed === 'boolean'
      && ['text', 'number', 'boolean'].includes(record.result_type ?? ''))));
}
export function confirmedDefinitionSave(value: unknown, catalog: boolean, expected?: { id: number; version: number; fields: Record<string, unknown> }): value is DepartmentRecord | CatalogRecord {
  return confirmedDefinition(value, catalog) && (!expected || (value.id === expected.id && value.version > expected.version && definitionMatches(value, expected.fields, catalog)));
}
export function canSubmitDefinition(draft: CatalogDraft, catalog: boolean, state: { confirming: boolean; hasSubmission: boolean; dirty: boolean; conflict: boolean; departmentError: string }): boolean {
  if (state.confirming) return state.hasSubmission;
  return Boolean(draft.name.trim() && (!catalog || (draft.departmentId && !state.departmentError))) && state.dirty && !state.conflict;
}
export const resultTypeNames: Record<ResultType, string> = { text: '文本', number: '数字', boolean: '是 / 否' };
export function availableDepartments(departments: Department[], retainedId: number | null = null): Department[] { return departments.filter(department => department.is_active !== false || department.id === retainedId); }
export function DefinitionFields({ draft, onChange, disabled, catalog, departments, editing }: {
  draft: CatalogDraft; onChange: (draft: CatalogDraft) => void; disabled: boolean; catalog: boolean; departments: Department[]; editing: boolean;
}) {
  return <>
    <label>{catalog ? '检查项目名称' : '科室名称'} *<input disabled={disabled} required maxLength={100} autoComplete="off" value={draft.name} onChange={event => onChange({ ...draft, name: event.target.value })} /></label>
    {catalog && <>
      <label>所属科室 *<select required disabled={disabled} value={draft.departmentId} onChange={event => onChange({ ...draft, departmentId: event.target.value })}><option value="">请选择科室</option>{availableDepartments(departments, editing ? Number(draft.departmentId) : null).map(department => <option key={department.id} value={department.id}>{department.name}{department.is_active === false ? '（已停用）' : ''}</option>)}</select></label>
      <label>项目分类<input disabled={disabled} maxLength={100} value={draft.category} onChange={event => onChange({ ...draft, category: event.target.value })} /><span className="field-hint">选填，例如常规检查、检验、影像。分类为文字说明。</span></label>
      <div className="field-grid"><label>单位<input disabled={disabled} maxLength={50} value={draft.unit} onChange={event => onChange({ ...draft, unit: event.target.value })} /></label><label>参考范围<input disabled={disabled} maxLength={500} value={draft.referenceRange} onChange={event => onChange({ ...draft, referenceRange: event.target.value })} /></label></div>
      <label>结果类型 *<select disabled={disabled} value={draft.resultType} onChange={event => onChange({ ...draft, resultType: event.target.value as ResultType })}>{Object.entries(resultTypeNames).map(([value, name]) => <option key={value} value={value}>{name}</option>)}</select></label>
      <label className="check-label"><input type="checkbox" disabled={disabled} checked={draft.abnormalAllowed} onChange={event => onChange({ ...draft, abnormalAllowed: event.target.checked })} />允许标记异常</label>
      <p className="field-hint">参考范围仅作说明，异常由医生核对后标记。</p>
    </>}
    {editing && <label className="check-label"><input type="checkbox" disabled={disabled} checked={draft.is_active} onChange={event => onChange({ ...draft, is_active: event.target.checked })} />启用{catalog ? '检查项目' : '科室'}</label>}
  </>;
}
