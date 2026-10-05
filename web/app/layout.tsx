import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = { title: 'Audli · Train your ears.', description: 'Your adaptive English listening companion.' };
export default function Layout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}
