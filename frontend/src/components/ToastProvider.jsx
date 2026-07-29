import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import '../styles/components.css';
import { formatToastType } from '../utils/presentation';

const ToastContext = createContext(null);
const toastDurationMs = 3000;

function makeToastId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const [liveMessage, setLiveMessage] = useState('');
  const timeoutMapRef = useRef(new Map());

  const dismissToast = useCallback((toastId) => {
    const timeoutId = timeoutMapRef.current.get(toastId);
    if (timeoutId) {
      window.clearTimeout(timeoutId);
      timeoutMapRef.current.delete(toastId);
    }

    setToasts((current) => current.filter((toast) => toast.id !== toastId));
  }, []);

  const showToast = useCallback((message, type = 'info') => {
    const nextToast = {
      id: makeToastId(),
      message: String(message ?? '').trim(),
      type,
    };

    if (!nextToast.message) {
      return '';
    }

    setToasts((current) => [...current, nextToast]);
    setLiveMessage(nextToast.message);

    const timeoutId = window.setTimeout(() => {
      dismissToast(nextToast.id);
    }, toastDurationMs);
    timeoutMapRef.current.set(nextToast.id, timeoutId);

    return nextToast.id;
  }, [dismissToast]);

  useEffect(() => () => {
    timeoutMapRef.current.forEach((timeoutId) => {
      window.clearTimeout(timeoutId);
    });
    timeoutMapRef.current.clear();
  }, []);

  const value = useMemo(() => ({
    showToast,
    dismissToast,
  }), [dismissToast, showToast]);

  return (
    <ToastContext.Provider value={value}>
      {children}

      <div className="sr-only" aria-live="polite" aria-atomic="true">
        {liveMessage}
      </div>

      <div className="toast-region" aria-label="通知">
        <div className="toast-stack">
          {toasts.map((toast) => (
            <div key={toast.id} className={`toast-card toast-${toast.type}`} role="status" aria-live="polite">
              <div className="toast-copy">
                <strong>{formatToastType(toast.type)}</strong>
                <p>{toast.message}</p>
              </div>
              <button
                type="button"
                className="toast-close"
                onClick={() => dismissToast(toast.id)}
                aria-label="关闭通知"
              >
                x
              </button>
            </div>
          ))}
        </div>
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error('useToast must be used within ToastProvider.');
  }
  return context;
}
