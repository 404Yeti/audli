import { AuthGate } from '../../../../components/auth-gate';
import { LessonSuccess } from '../../../../components/listening/LessonSuccess';

export default async function SuccessPage({ params, searchParams }: { params: Promise<{ lessonId: string }>; searchParams: Promise<{ attempt?: string | string[] }> }) {
  const { lessonId } = await params;
  const { attempt } = await searchParams;
  return <AuthGate><LessonSuccess lessonId={lessonId} attemptId={Array.isArray(attempt) ? attempt[0] : attempt} /></AuthGate>;
}
