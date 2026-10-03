import type { Patient, ExamStatus } from './types';
export const roleNames = { admin: '管理员', registrar: '登记员', doctor: '科室医生', reviewer: '总检医生' };
export const statusNames: Record<ExamStatus, string> = { registered: '待检', in_progress: '体检中', ready_for_review: '待总检', completed: '已完成' };
export function sexName(sex: Patient['sex']) { return { male: '男', female: '女', other: '其他' }[sex]; }
export function dateTime(value?: string | null) { if (!value) return '—'; const d = new Date(value); return Number.isNaN(d.getTime()) ? value : d.toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' }); }
export function Status({ value }: { value: ExamStatus }) { return <span className={`badge status-${value}`}>{statusNames[value]}</span>; }
export function ErrorBox({ message }: { message: string }) { return message ? <div className="notice error" role="alert">{message}</div> : null; }
export function Pagination({ page, total, busy, onPage }: { page: number; total: number; busy: boolean; onPage: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / 20));
  return <div className="pagination"><span>共 {total} 条 · 每页 20 条</span><div><button type="button" disabled={page <= 1 || busy} onClick={() => onPage(page - 1)}>上一页</button><span className="page-number">{page} / {pages}</span><button type="button" disabled={page >= pages || busy} onClick={() => onPage(page + 1)}>下一页</button></div></div>;
}
export function Loading() { return <div className="loading" role="status">正在加载…</div>; }
export function PatientSummary({ patient }: { patient: Patient }) { return <><strong>{patient.name}</strong><span className="muted">{sexName(patient.sex)} · {patient.birth_date || '出生日期未填'}</span></>; }

