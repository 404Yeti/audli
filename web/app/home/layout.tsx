import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { listeningEnabled } from '../../lib/listening-flag.ts';
import '@fontsource/fredoka/600.css';
import '@fontsource/nunito/400.css';
import '@fontsource/nunito/600.css';
import '@fontsource/nunito/700.css';
import '@fontsource/nunito/800.css';

export const metadata: Metadata = { title: 'Audli · Eyes optional. Ears essential.', description: 'Short listening lessons at your level, in the accent you choose. Listen first, then answer four quick exercises.' };
export default function HomeLayout({ children }: { children: React.ReactNode }) {
  if (!listeningEnabled()) notFound();        // the new home exists only where Audli 2.0 is switched on; "/" reaches it through the cutover rewrite
  return children;
}
