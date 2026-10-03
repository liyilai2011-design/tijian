import React from 'react';
import type { Role } from '../types';

type MenuPage = 'patients' | 'exams' | 'users' | 'catalog' | 'packages';

export function RoleNavigation({ role, page, onNavigate }: {
  role: Role;
  page: string;
  onNavigate: (page: MenuPage) => void;
}) {
  return <nav>
    {(role === 'admin' || role === 'registrar') && <button className={page === 'patients' || page === 'patient-detail' ? 'active' : ''} onClick={() => onNavigate('patients')}>受检者登记</button>}
    <button className={page !== 'patients' && page !== 'patient-detail' && page !== 'users' && page !== 'catalog' && page !== 'packages' ? 'active' : ''} onClick={() => onNavigate('exams')}>{role === 'doctor' ? '本科室待检' : role === 'reviewer' ? '总检工作台' : '体检管理'}</button>
    {role === 'admin' && <button className={page === 'catalog' ? 'active' : ''} onClick={() => onNavigate('catalog')}>科室和项目</button>}
    {role === 'admin' && <button className={page === 'packages' ? 'active' : ''} onClick={() => onNavigate('packages')}>套餐管理</button>}
    {role === 'admin' && <button className={page === 'users' ? 'active' : ''} onClick={() => onNavigate('users')}>账号管理</button>}
  </nav>;
}
