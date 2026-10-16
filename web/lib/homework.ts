export type HomeworkAssignment = {
  id: string; lesson_id: string;
  status: 'offered' | 'assigned' | 'completed' | 'discussed' | 'skipped' | 'unavailable';
  objective: string; message?: string | null;
  content: null | { provider: string; provider_id: string; canonical_url: string; title: string; publisher: string; duration_seconds: number; validated_at: string };
};
export type HomeworkView = { enabled: boolean; assignments: HomeworkAssignment[] };
export function verifiedHomeworkUrl(item: HomeworkAssignment): string | null {
  const content = item.content;
  if (!content || content.provider !== 'youtube' || !/^[A-Za-z0-9_-]{11}$/.test(content.provider_id)) return null;
  const expected = `https://www.youtube.com/watch?v=${content.provider_id}`;
  return content.canonical_url === expected ? expected : null;
}
