export type ConnectionState = 'checking' | 'connected' | 'failed';

export function ConnectionStatus({ state, onRetry, compact = false }: {
  state: ConnectionState;
  onRetry: () => void;
  compact?: boolean;
}) {
  const checking = state === 'checking';
  const text = {
    checking: '正在检查连接…',
    connected: '服务器已连接',
    failed: '服务器连接失败',
  }[state];
  return <div className={`connection-status connection-${state}${compact ? ' connection-compact' : ''}`}>
    <div className="connection-text" role="status" aria-live="polite">
      <span className="connection-dot" aria-hidden="true" />
      <span>{text}</span>
    </div>
    <button type="button" disabled={checking} onClick={onRetry}>
      {checking ? '检查中…' : state === 'failed' ? '重试连接' : '重新检查'}
    </button>
    {!compact && state === 'failed' && <p>请检查局域网连接，然后重试。</p>}
  </div>;
}
