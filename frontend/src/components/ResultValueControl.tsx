import type { ExamItem, ResultType } from '../types';

export function resultTypeFor(item: Pick<ExamItem, 'result_type'>): ResultType { return item.result_type === 'number' || item.result_type === 'boolean' ? item.result_type : 'text'; }
export function validResultValue(value: string, type: ResultType): boolean {
  const trimmed = value.trim();
  if (!trimmed) return false;
  if (type === 'boolean') return trimmed === '是' || trimmed === '否';
  if (type === 'number') return /^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$/.test(trimmed) && Number.isFinite(Number(trimmed));
  return trimmed.length <= 5000;
}
export function ResultValueControl({ type, value, disabled, onChange }: { type: ResultType; value: string; disabled: boolean; onChange: (value: string) => void }) {
  if (type === 'number') return <input disabled={disabled} type="number" step="any" required value={value} onChange={event => onChange(event.target.value)} />;
  if (type === 'boolean') return <select disabled={disabled} required value={value} onChange={event => onChange(event.target.value)}><option value="">请选择</option><option value="是">是</option><option value="否">否</option></select>;
  return <textarea disabled={disabled} rows={2} required maxLength={5000} value={value} onChange={event => onChange(event.target.value)} />;
}
export function AbnormalControl({ allowed, value, disabled, onChange }: { allowed: boolean; value: boolean; disabled: boolean; onChange: (value: boolean) => void }) {
  return allowed ? <label className="check-label"><input disabled={disabled} type="checkbox" checked={value} onChange={event => onChange(event.target.checked)} /> 标记异常</label> : <span className="muted">本项目不标记异常</span>;
}
