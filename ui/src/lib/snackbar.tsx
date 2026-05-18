'use client';

/**
 * Lightweight toast/snackbar provider.
 *
 * Usage:
 *   const { notify } = useSnackbar();
 *   notify({ tone: 'error', title: 'Oops', message: '…' });
 *
 *   // Or the shortcuts:
 *   notify.error('Mensaje');
 *   notify.success('Mensaje');
 *   notify.warning({ title: 'Espera', message: '…' });
 *
 * Toasts stack top-right, auto-dismiss after `ttlMs` (default 5s for
 * info/success, 8s for warning/error). Click the × to dismiss earlier.
 *
 * No third-party deps; styled with the existing glass / Tailwind tokens.
 */

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Info,
  X as XIcon,
} from 'lucide-react';
import { useTranslations } from 'next-intl';

export type SnackbarTone = 'success' | 'info' | 'warning' | 'error';

export interface SnackbarOptions {
  tone?: SnackbarTone;
  title?: string;
  message: string;
  /** Override the auto-dismiss timer (ms). 0 = never auto-dismiss. */
  ttlMs?: number;
}

interface SnackbarItem extends Required<Omit<SnackbarOptions, 'title'>> {
  id: number;
  title?: string;
}

interface SnackbarShortcuts {
  (options: SnackbarOptions): void;
  success: (msg: string | SnackbarOptions) => void;
  info:    (msg: string | SnackbarOptions) => void;
  warning: (msg: string | SnackbarOptions) => void;
  error:   (msg: string | SnackbarOptions) => void;
}

interface SnackbarContextValue {
  notify: SnackbarShortcuts;
  dismiss: (id: number) => void;
}

const SnackbarContext = createContext<SnackbarContextValue | null>(null);

const DEFAULT_TTL: Record<SnackbarTone, number> = {
  success: 4_000,
  info:    4_000,
  warning: 8_000,
  error:   8_000,
};

// ── Provider ─────────────────────────────────────────────────
export function SnackbarProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<SnackbarItem[]>([]);
  const nextIdRef = useRef(1);

  const dismiss = useCallback((id: number) => {
    setItems((xs) => xs.filter((x) => x.id !== id));
  }, []);

  const push = useCallback((options: SnackbarOptions) => {
    const tone = options.tone ?? 'info';
    const ttlMs = options.ttlMs ?? DEFAULT_TTL[tone];
    const id = nextIdRef.current++;
    const item: SnackbarItem = {
      id,
      tone,
      title: options.title,
      message: options.message,
      ttlMs,
    };
    setItems((xs) => [...xs, item]);
    if (ttlMs > 0) {
      setTimeout(() => dismiss(id), ttlMs);
    }
  }, [dismiss]);

  const notify = useMemo<SnackbarShortcuts>(() => {
    const base = ((options: SnackbarOptions) => push(options)) as SnackbarShortcuts;
    const mk = (tone: SnackbarTone) => (msg: string | SnackbarOptions) =>
      push(typeof msg === 'string' ? { tone, message: msg } : { ...msg, tone });
    base.success = mk('success');
    base.info    = mk('info');
    base.warning = mk('warning');
    base.error   = mk('error');
    return base;
  }, [push]);

  const ctx = useMemo(() => ({ notify, dismiss }), [notify, dismiss]);

  return (
    <SnackbarContext.Provider value={ctx}>
      {children}
      <SnackbarStack items={items} onDismiss={dismiss} />
    </SnackbarContext.Provider>
  );
}

// ── Hook ─────────────────────────────────────────────────────
export function useSnackbar(): SnackbarContextValue {
  const ctx = useContext(SnackbarContext);
  if (!ctx) {
    throw new Error('useSnackbar must be used inside <SnackbarProvider>');
  }
  return ctx;
}

// ── Renderer ─────────────────────────────────────────────────
function SnackbarStack({
  items,
  onDismiss,
}: {
  items: SnackbarItem[];
  onDismiss: (id: number) => void;
}) {
  return (
    <div className="fixed top-4 right-4 z-[100] flex flex-col gap-2 pointer-events-none max-w-sm w-[min(100%-2rem,24rem)]">
      {items.map((item) => (
        <SnackbarRow key={item.id} item={item} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

const TONE_CLASSES: Record<SnackbarTone, string> = {
  success: 'border-green-500/50 bg-green-900/30',
  info:    'border-blue-500/50 bg-blue-900/30',
  warning: 'border-amber-500/50 bg-amber-900/30',
  error:   'border-red-500/50 bg-red-900/30',
};

const TONE_TEXT: Record<SnackbarTone, string> = {
  success: 'text-green-200',
  info:    'text-blue-200',
  warning: 'text-amber-200',
  error:   'text-red-200',
};

function ToneIcon({ tone }: { tone: SnackbarTone }) {
  const cls = `w-5 h-5 flex-shrink-0 ${TONE_TEXT[tone]}`;
  switch (tone) {
    case 'success': return <CheckCircle2 className={cls} />;
    case 'warning': return <AlertTriangle className={cls} />;
    case 'error':   return <XCircle className={cls} />;
    default:        return <Info className={cls} />;
  }
}

function SnackbarRow({
  item,
  onDismiss,
}: {
  item: SnackbarItem;
  onDismiss: (id: number) => void;
}) {
  const t = useTranslations('common');
  // Slide-in animation on mount via a small mount flag.
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    const r = requestAnimationFrame(() => setMounted(true));
    return () => cancelAnimationFrame(r);
  }, []);

  return (
    <div
      className={`pointer-events-auto backdrop-blur-lg border rounded-lg shadow-2xl
        ${TONE_CLASSES[item.tone]}
        transition-all duration-300 ease-out
        ${mounted ? 'opacity-100 translate-x-0' : 'opacity-0 translate-x-4'}`}
      role={item.tone === 'error' || item.tone === 'warning' ? 'alert' : 'status'}
      aria-live={item.tone === 'error' ? 'assertive' : 'polite'}
    >
      <div className="flex items-start gap-3 p-3 pr-2">
        <ToneIcon tone={item.tone} />
        <div className="flex-1 min-w-0">
          {item.title && (
            <p className={`text-sm font-semibold ${TONE_TEXT[item.tone]}`}>
              {item.title}
            </p>
          )}
          <p className="text-sm text-white/85 whitespace-pre-wrap break-words leading-snug mt-0.5">
            {item.message}
          </p>
        </div>
        <button
          onClick={() => onDismiss(item.id)}
          aria-label={t('close')}
          className="text-white/40 hover:text-white/80 transition-colors p-1 -m-1"
        >
          <XIcon className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}
