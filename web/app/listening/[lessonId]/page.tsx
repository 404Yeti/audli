import { AuthGate } from '../../../components/auth-gate';
import { ListeningLesson } from '../../../components/listening/ListeningLesson';

export default async function LessonPage({ params }: { params: Promise<{ lessonId: string }> }) {
  const { lessonId } = await params;
  return <AuthGate><ListeningLesson lessonId={lessonId} /></AuthGate>;
}
