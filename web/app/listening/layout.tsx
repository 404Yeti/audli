import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { listeningEnabled } from '../../lib/listening-flag.ts';
import '@fontsource/fredoka/600.css';
import '@fontsource/nunito/400.css';
import '@fontsource/nunito/600.css';
import '@fontsource/nunito/700.css';
import '@fontsource/nunito/800.css';

export const metadata: Metadata = { title: 'Listening lessons · Audli', description: 'Eyes optional. Ears essential.' };
export default function ListeningLayout({ children }: { children: React.ReactNode }) {
  if (!listeningEnabled()) notFound();        // off unless NEXT_PUBLIC_AUDLI_LISTENING=true: nothing of 2.0 is reachable in production
  return children;
}
