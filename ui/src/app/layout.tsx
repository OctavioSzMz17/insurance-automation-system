import type { Metadata, Viewport } from 'next';
import '../styles/globals.css';
import { SnackbarProvider } from '@/lib/snackbar';
import { LocaleProvider } from '@/lib/i18n';

export const metadata: Metadata = {
  title: 'InsureAI · Adjuster Console',
  description: 'AI-assisted claim review platform',
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // The <html lang="..."> attribute is updated client-side by LocaleProvider
  // once the user's preferred locale is detected.
  return (
    <html lang="es" suppressHydrationWarning>
      <body>
        <LocaleProvider>
          <SnackbarProvider>
            <main className="min-h-screen flex flex-col items-center p-4 pt-0">
              {children}
            </main>
          </SnackbarProvider>
        </LocaleProvider>
      </body>
    </html>
  );
}
