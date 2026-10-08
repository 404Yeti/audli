import type { Metadata } from 'next';
import '@fontsource/inter/400.css';
import '@fontsource/inter/600.css';
import '@fontsource/inter/700.css';
import './globals.css';

export const metadata: Metadata = {
  title: 'Audli · Eyes optional. Ears essential.',
  description: 'AI-powered English listening practice that adapts to your level, pace, and understanding.',
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
