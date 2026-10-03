import type { CatalogRecord, PackageDetail } from '../types';

export type PackageDraft = { name: string; description: string; is_active: boolean; items: CatalogRecord[] };
export const blankPackage = (): PackageDraft => ({ name: '', description: '', is_active: true, items: [] });
export function packageDraft(record: PackageDetail): PackageDraft { return { name: record.name, description: record.description, is_active: record.is_active, items: record.items }; }
export function packagePayload(draft: PackageDraft) { return { name: draft.name.trim(), description: draft.description.trim(), is_active: draft.is_active, item_ids: draft.items.map(item => item.id).sort((a, b) => a - b) }; }
export function confirmedManagedPackage(value: unknown): value is PackageDetail {
  if (!value || typeof value !== 'object') return false;
  const record = value as Partial<PackageDetail>;
  return Boolean(Number.isSafeInteger(record.id) && (record.id ?? 0) > 0 && Number.isSafeInteger(record.version) && (record.version ?? 0) > 0 && typeof record.name === 'string' && record.name.trim()
    && typeof record.description === 'string' && typeof record.is_active === 'boolean' && typeof record.is_available === 'boolean' && typeof record.unavailable_reason === 'string'
    && Array.isArray(record.items) && record.items.length <= 200 && record.item_count === record.items.length && Array.isArray(record.item_ids) && record.item_ids.length === record.items.length
    && new Set(record.item_ids).size === record.item_ids.length && new Set(record.items.map(item => item.id)).size === record.items.length
    && record.items.every(item => Number.isSafeInteger(item.id) && item.id > 0 && typeof item.name === 'string' && record.item_ids!.includes(item.id)));
}
export function packageMatches(record: PackageDetail, fields: ReturnType<typeof packagePayload>): boolean {
  const current = packagePayload(packageDraft(record)); return current.name === fields.name && current.description === fields.description && current.is_active === fields.is_active && JSON.stringify(current.item_ids) === JSON.stringify(fields.item_ids);
}
export function confirmedPackageSave(value: unknown, expected?: { id: number; version: number; fields: ReturnType<typeof packagePayload> }): value is PackageDetail {
  return confirmedManagedPackage(value) && (!expected || (value.id === expected.id && value.version > expected.version && packageMatches(value, expected.fields)));
}
export function canSubmitPackage(draft: PackageDraft, editing: PackageDetail | null, state: { confirming: boolean; hasSubmission: boolean; dirty: boolean; conflict: boolean }): boolean {
  if (state.confirming) return state.hasSubmission;
  const ids = draft.items.map(item => item.id); const validIds = ids.length <= 200 && new Set(ids).size === ids.length && ids.every(id => Number.isSafeInteger(id) && id > 0);
  const allowedEmpty = Boolean(editing && (!draft.is_active || (editing.is_active && editing.item_ids.length === 0)));
  return Boolean(draft.name.trim() && validIds && (ids.length > 0 || allowedEmpty) && state.dirty && !state.conflict);
}
export function PackageFields({ draft, disabled, onChange }: { draft: PackageDraft; disabled: boolean; onChange: (draft: PackageDraft) => void }) {
  return <><label>套餐名称 *<input disabled={disabled} required maxLength={100} autoComplete="off" value={draft.name} onChange={event => onChange({ ...draft, name: event.target.value })} /></label><label>套餐说明<textarea disabled={disabled} rows={3} maxLength={2000} value={draft.description} onChange={event => onChange({ ...draft, description: event.target.value })} /></label><label className="check-label"><input disabled={disabled} type="checkbox" checked={draft.is_active} onChange={event => onChange({ ...draft, is_active: event.target.checked })} />启用套餐</label></>;
}
