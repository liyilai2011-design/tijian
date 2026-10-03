import React, { Component, Suspense, lazy, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api, ApiError, checkServerConnection, clearRecoveredNetworkError, errorMessage, setCsrf } from './api';
import type { Auth, User } from './types';
import { ErrorBox, Loading, roleNames } from './ui';
import { ConnectionStatus, type ConnectionState } from './components/ConnectionStatus';
import { RoleNavigation } from './components/RoleNavigation';
import './styles.css';
const Patients = lazy(() => import('./pages/Patients'));
const Exams = lazy(() => import('./pages/Exams'));
const ExamDetail = lazy(() => import('./pages/ExamDetail'));
const ReportView = lazy(() => import('./pages/Report'));
const Users = lazy(() => import('./pages/Users'));
const PatientDetail = lazy(() => import('./pages/PatientDetail'));
const Catalog = lazy(() => import('./pages/Catalog'));
const Packages = lazy(() => import('./pages/Packages'));
type Route = { page: 'patients' | 'patient-detail' | 'exams' | 'detail' | 'report' | 'users' | 'catalog' | 'packages'; id?: number };

function Login({ onLogin, expired = false, connection, onRetryConnection, sessionError = '' }: {
  onLogin: (auth: Auth) => void;
  expired?: boolean;
  connection: ConnectionState;
  onRetryConnection: () => void;
  sessionError?: string;
}) {
  const [username, setUsername] = useState(''); const [password, setPassword] = useState('');
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setError(''); setBusy(true);
    try { const auth = await api<Auth>('/auth/login', { method: 'POST', body: { username, password } }); setPassword(''); onLogin(auth); }
    catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  return <div className={expired ? 'login-overlay' : 'login-page'}>
    <section className="login-card">
      <div className="brand-mark">体</div>
      <p className="eyebrow">体检中心工作平台</p>
      <h1>{expired ? '重新登录' : '欢迎登录'}</h1>
      <p className="muted">{expired ? '会话已过期。重新登录后可继续操作，当前输入已保留。' : '使用工作人员账号进入体检工作台。'}</p>
      <ConnectionStatus state={connection} onRetry={onRetryConnection} />
      <ErrorBox message={sessionError} />
      <form onSubmit={submit}>
        <label>账号<input disabled={busy} required autoFocus name="username" autoComplete="username" maxLength={64} value={username} onChange={e => setUsername(e.target.value)} /></label>
        <label>密码<input disabled={busy} required type="password" name="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} /></label>
        <ErrorBox message={error} />
        <button className="primary full" disabled={busy}>{busy ? '正在登录…' : '登录系统'}</button>
      </form>
      <p className="login-footer">内部工作系统 · 体检信息需妥善保管</p>
    </section>
  </div>;
}
class Boundary extends Component<{ children: React.ReactNode }, { error: boolean }> {
  state = { error: false };
  static getDerivedStateFromError() { return { error: true }; }
  render() { return this.state.error ? <div className="card"><h2>页面暂时无法显示</h2><p>请检查服务器连接。重新加载页面前，请注意未保存的输入可能会丢失。</p><button onClick={() => window.location.reload()}>重新加载页面</button></div> : this.props.children; }
}
function App() {
  const [user, setUser] = useState<User | null>(null); const [starting, setStarting] = useState(true);
  const [initialError, setInitialError] = useState<Error | null>(null); const [expired, setExpired] = useState(false); const [offline, setOffline] = useState(false);
  const [connection, setConnection] = useState<ConnectionState>('checking');
  const checkingConnection = useRef(false);
  const [route, setRoute] = useState<Route>({ page: 'exams' }); const [logoutError, setLogoutError] = useState('');
  async function checkConnection() {
    if (checkingConnection.current) return;
    checkingConnection.current = true;
    setConnection('checking');
    try {
      await checkServerConnection();
      setConnection('connected'); setOffline(false); setInitialError(clearRecoveredNetworkError);
    } catch {
      setConnection('failed'); setOffline(true);
    } finally {
      checkingConnection.current = false;
    }
  }
  async function checkSession() {
    setStarting(true); setInitialError(null);
    try { const auth = await api<Auth>('/auth/me'); onLogin(auth); }
    catch (err) { if (!(err instanceof ApiError && err.status === 401)) setInitialError(err instanceof Error ? err : new Error(errorMessage(err))); }
    finally { setStarting(false); }
  }
  useEffect(() => {
    const fail = () => {
      setOffline(true);
      if (!checkingConnection.current) setConnection('failed');
    }, expire = () => setExpired(true);
    const recovered = () => {
      setOffline(false);
      if (!checkingConnection.current) setConnection('connected');
    };
    window.addEventListener('api-offline', fail); window.addEventListener('session-expired', expire);
    window.addEventListener('api-online', recovered);
    window.addEventListener('offline', fail);
    void checkSession();
    void checkConnection();
    return () => { window.removeEventListener('api-offline', fail); window.removeEventListener('session-expired', expire); window.removeEventListener('api-online', recovered); window.removeEventListener('offline', fail); };
  }, []);
  function onLogin(auth: Auth) {
    setCsrf(auth.csrf_token); setExpired(false);
    if (!user || user.id !== auth.user.id || user.role !== auth.user.role || user.department_id !== auth.user.department_id) {
      setRoute({ page: auth.user.role === 'registrar' ? 'patients' : 'exams' });
    }
    setUser(auth.user);
  }
  async function logout() {
    if (!window.dispatchEvent(new CustomEvent('before-page-change', { cancelable: true }))) return;
    if (!window.confirm('退出登录？请先确认当前输入已经保存。')) return;
    setLogoutError('');
    try { await api('/auth/logout', { method: 'POST', body: {} }); setCsrf(''); setUser(null); setExpired(false); }
    catch (err) { setLogoutError(errorMessage(err)); }
  }
  function navigate(next: Route) {
    // Each edit page installs a guard only while there are unsaved changes.
    const event = new CustomEvent('before-page-change', { cancelable: true });
    if (window.dispatchEvent(event)) setRoute(next);
  }
  const retryConnection = () => { void checkConnection(); };
  if (starting) return <div className="login-page"><section className="login-card"><ConnectionStatus state={connection} onRetry={retryConnection} /><Loading /></section></div>;
  if (!user) return <Login onLogin={onLogin} connection={connection} onRetryConnection={retryConnection} sessionError={initialError?.message || ''} />;
  const permissionKey = `${user.id}:${user.role}:${user.department_id ?? ''}`;
  const registration = user.role === 'admin' || user.role === 'registrar';
  const titles = { patients: '受检者登记', 'patient-detail': '受检者档案', exams: user.role === 'doctor' ? '本科室待检' : user.role === 'reviewer' ? '总检工作台' : '体检管理', detail: '体检详情', report: '体检报告', users: '账号管理', catalog: '科室和项目', packages: '套餐管理' };
  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark small">体</span><div>体检中心<small>工作平台</small></div></div>
      <p className="nav-caption">工作导航</p>
      <RoleNavigation role={user.role} page={route.page} onNavigate={page => navigate({ page })} />
      <div className="sidebar-footer">局域网工作系统<small>所有数据由服务器统一保存</small></div>
    </aside>
    <div className="workspace">
      <header className="topbar">
        <div className="topbar-heading"><span className="topbar-page-title">{titles[route.page]}</span><ConnectionStatus state={connection} onRetry={retryConnection} compact /></div>
        <div><strong>{user.display_name}</strong><span className="role-label">{roleNames[user.role]}</span><button className="text-button" onClick={() => void logout()}>退出</button></div>
      </header>
      {offline && <div className="network-banner" role="alert">服务器连接失败，请检查局域网连接。未保存的输入仍保留在当前页面，请勿关闭浏览器。
        <button disabled={connection === 'checking'} onClick={retryConnection}>{connection === 'checking' ? '检查中…' : '重试连接'}</button>
      </div>}
      <main>
        <ErrorBox message={logoutError} />
        <Boundary key={permissionKey}>
          <Suspense fallback={<Loading />}>
            <div key={permissionKey}>
              {route.page === 'patients' && registration && <Patients user={user} onPatient={id => navigate({ page: 'patient-detail', id })} onExam={id => navigate({ page: 'detail', id })} />}
              {route.page === 'patient-detail' && registration && <PatientDetail key={route.id} id={route.id!} onBack={() => navigate({ page: 'patients' })} onExam={id => navigate({ page: 'detail', id })} />}
              {route.page === 'exams' && <Exams user={user} onOpen={id => navigate({ page: 'detail', id })} />}
              {route.page === 'detail' && <ExamDetail key={route.id} id={route.id!} user={user} onBack={() => navigate({ page: 'exams' })} onReport={() => navigate({ page: 'report', id: route.id })} />}
              {route.page === 'report' && <ReportView id={route.id!} onBack={() => navigate({ page: 'detail', id: route.id })} />}
              {route.page === 'catalog' && user.role === 'admin' && <Catalog />}
              {route.page === 'packages' && user.role === 'admin' && <Packages />}
              {route.page === 'users' && user.role === 'admin' && <Users user={user} onSelfUpdated={updated => setUser(current =>
                current && current.id === updated.id && current.role === updated.role && current.department_id === updated.department_id
                  ? { ...current, display_name: updated.display_name } : current
              )} />}
            </div>
          </Suspense>
        </Boundary>
      </main>
    </div>
    {expired && <Login expired onLogin={onLogin} connection={connection} onRetryConnection={retryConnection} />}
  </div>;
}
createRoot(document.getElementById('root')!).render(<App />);

