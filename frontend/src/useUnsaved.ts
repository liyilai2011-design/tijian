import { useEffect } from 'react';
export function useUnsaved(dirty: boolean, locked = false, lockedMessage = '登记结果尚未确认，请保持当前页面并重试核对，避免重复登记。') {
  useEffect(() => {
    if (!dirty) return;
    const unload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    const navigate = (event: Event) => {
      if (locked) { event.preventDefault(); window.alert(lockedMessage); }
      else if (!window.confirm('当前有未保存的输入，离开页面会丢失这些内容。确定离开？')) event.preventDefault();
    };
    window.addEventListener('beforeunload', unload); window.addEventListener('before-page-change', navigate);
    return () => { window.removeEventListener('beforeunload', unload); window.removeEventListener('before-page-change', navigate); };
  }, [dirty, locked, lockedMessage]);
}
