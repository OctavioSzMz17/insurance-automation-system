'use client';

/**
 * Locale provider + language toggle, built on top of `next-intl`.
 *
 * Mode: client-only (no URL-based routing). We pick the locale on mount
 * from `localStorage`, falling back to the navigator language, falling
 * back to Spanish. The user can flip it at any time with the toggle in
 * the header; the choice persists across reloads.
 *
 * All translations are bundled — nothing leaves the browser. Zero
 * external requests, zero telemetry.
 */

import { NextIntlClientProvider, AbstractIntlMessages } from 'next-intl';
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';
import { Globe } from 'lucide-react';

import enMessages from '../../messages/en.json';
import esMessages from '../../messages/es.json';

export type Locale = 'en' | 'es';

const SUPPORTED: readonly Locale[] = ['en', 'es'] as const;
const DEFAULT_LOCALE: Locale = 'es';
const STORAGE_KEY = 'insureai.locale';

const MESSAGES: Record<Locale, AbstractIntlMessages> = {
  en: enMessages as AbstractIntlMessages,
  es: esMessages as AbstractIntlMessages,
};

// ── Context ──────────────────────────────────────────────────
interface LocaleContextValue {
  locale: Locale;
  setLocale: (l: Locale) => void;
  toggle: () => void;
}
const LocaleContext = createContext<LocaleContextValue | null>(null);

function detectInitialLocale(): Locale {
  if (typeof window === 'undefined') return DEFAULT_LOCALE;
  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (stored === 'en' || stored === 'es') return stored;
  const nav = window.navigator.language?.slice(0, 2).toLowerCase();
  if (nav === 'en' || nav === 'es') return nav;
  return DEFAULT_LOCALE;
}

// ── Provider ─────────────────────────────────────────────────
export function LocaleProvider({ children }: { children: React.ReactNode }) {
  // Start with the SSR-safe default so the server-rendered markup
  // matches the first client render; hydrate the real choice after mount.
  const [locale, setLocaleState] = useState<Locale>(DEFAULT_LOCALE);

  useEffect(() => {
    const initial = detectInitialLocale();
    if (initial !== locale) setLocaleState(initial);
    // also reflect on <html lang>
    document.documentElement.lang = initial;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const setLocale = useCallback((l: Locale) => {
    if (!SUPPORTED.includes(l)) return;
    setLocaleState(l);
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(STORAGE_KEY, l);
      document.documentElement.lang = l;
    }
  }, []);

  const toggle = useCallback(() => {
    setLocale(locale === 'es' ? 'en' : 'es');
  }, [locale, setLocale]);

  const ctx = useMemo(
    () => ({ locale, setLocale, toggle }),
    [locale, setLocale, toggle],
  );

  return (
    <LocaleContext.Provider value={ctx}>
      <NextIntlClientProvider
        locale={locale}
        messages={MESSAGES[locale]}
        timeZone="America/Mexico_City"
        // Re-mount the tree when locale flips so every useTranslations()
        // call re-reads from the right dictionary.
        key={locale}
      >
        {children}
      </NextIntlClientProvider>
    </LocaleContext.Provider>
  );
}

// ── Hooks ────────────────────────────────────────────────────
export function useLocale(): LocaleContextValue {
  const ctx = useContext(LocaleContext);
  if (!ctx) {
    throw new Error('useLocale must be used inside <LocaleProvider>');
  }
  return ctx;
}

// ── Toggle UI ────────────────────────────────────────────────
export function LanguageToggle({ className = '' }: { className?: string }) {
  const { locale, toggle } = useLocale();
  const next: Locale = locale === 'es' ? 'en' : 'es';
  const nextLabel = next.toUpperCase();
  const title = locale === 'es' ? 'Switch to English' : 'Cambiar a Español';

  return (
    <button
      onClick={toggle}
      title={title}
      aria-label={title}
      className={`flex items-center gap-2 px-3 py-2 glass-sm hover:bg-white/10 transition-colors rounded-lg text-white/70 text-sm ${className}`}
    >
      <Globe className="w-4 h-4" />
      <span className="font-semibold text-xs tracking-wider">{nextLabel}</span>
    </button>
  );
}
