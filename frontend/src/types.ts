export type Role = 'admin' | 'registrar' | 'doctor' | 'reviewer';
export interface User { id: number; username: string; display_name: string; role: Role; department_id: number | null }
export interface ManagedUser extends User { is_active: boolean }
export interface UserState { display_name: string; role: Role; department_id: number | null; is_active: boolean }
export interface Auth { user: User; csrf_token: string }
export interface Patient { id: number; name: string; sex: 'male' | 'female' | 'other'; birth_date: string | null; phone: string | null; created_at?: string }
export interface PatientRecord extends Patient {
  age: number | null;
  identity_number: string | null;
  ethnicity: string | null;
  address: string | null;
  employer: string | null;
  note: string | null;
  created_at: string;
  updated_at: string;
  version: number;
}
export interface Department { id: number; name: string; is_active?: boolean }
export interface DepartmentRecord extends Department { is_active: boolean; version: number }
export type ResultType = 'text' | 'number' | 'boolean';
export interface CatalogRecord { id: number; name: string; department_id: number; department_name: string; department_is_active?: boolean; category: string; unit: string | null; reference_range: string | null; result_type: ResultType; abnormal_allowed: boolean; is_active: boolean; version: number }
export interface Package { id: number; name: string; description: string; items: { id: number; name: string; department_id: number; department_name: string; unit: string | null; reference_range: string | null }[] }
export interface PackageSummary { id: number; name: string; description: string; is_active: boolean; version: number; item_count: number; is_available: boolean; unavailable_reason: string }
export interface PackageDetail extends PackageSummary { items: CatalogRecord[]; item_ids: number[] }
export type ExamStatus = 'registered' | 'in_progress' | 'ready_for_review' | 'completed';
export interface Exam { id: number; exam_number: string; exam_date: string; exam_type: string; patient: Patient; package_name: string; status: ExamStatus; created_at: string; completed_count: number; total_count: number; version: number }
export interface RegistrationOptions { today: string; exam_types: string[] }
export interface RegistrationResult { patient: PatientRecord; exam: ExamDetail; reused_patient: boolean }
export interface ExamItem { id: number; item_name: string; department_id: number; department_name: string; category?: string; result_type?: ResultType; abnormal_allowed?: boolean; unit: string | null; reference_range: string | null; value: string | null; is_abnormal: boolean; note: string | null; status: 'pending' | 'completed'; version: number }
export interface ExamDetail extends Exam { items: ExamItem[]; note?: string | null; conclusion: string | null; completed_at: string | null; latest_report_revision: number | null }
export interface Report { exam_id: number; exam_number: string; revision: number; issued_at: string; patient: Patient; package_name: string; items: ExamItem[]; conclusion: string; reviewer_name: string }
export interface History { id: number; version: number; value: string; is_abnormal: boolean; note: string | null; changed_by: string | number; changed_at: string }
export interface Page<T> { items: T[]; total: number; page: number; page_size: number }
