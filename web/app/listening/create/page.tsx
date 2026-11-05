import { notFound } from 'next/navigation';
import { AuthGate } from '../../../components/auth-gate';
import { CreateLesson } from '../../../components/listening/CreateLesson';

// Present only where NEXT_PUBLIC_AUDLI_LISTENING_GENERATOR=true: the screen needs the generator on the server too.
export default function Page() {
  if (process.env.NEXT_PUBLIC_AUDLI_LISTENING_GENERATOR !== 'true') notFound();
  return <AuthGate><CreateLesson /></AuthGate>;
}
