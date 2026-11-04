import type { Metadata } from 'next';
import '@fontsource/fredoka/600.css';
import '@fontsource/nunito/400.css';
import '@fontsource/nunito/600.css';
import '@fontsource/nunito/700.css';
import '@fontsource/nunito/800.css';

export const metadata: Metadata = { title: 'Listening lessons · Audli', description: 'Eyes optional. Ears essential.' };
export default function ListeningLayout({ children }: { children: React.ReactNode }) { return children; }
