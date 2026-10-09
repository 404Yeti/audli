import type { Page, Route } from '@playwright/test';
import type { LessonCheckpoint } from '../lib/lesson-lifecycle';

/** Existing media regressions start at a saved EXERCISE checkpoint.
 * New lifecycle tests exercise welcome/review/closing separately. */
export class ExerciseLessonFixture {
  private value: LessonCheckpoint | null = null;
  private anchor = 0;
  constructor(private page: Page, private current: () => LessonCheckpoint['exercise'], private generate: () => LessonCheckpoint['exercise']) {}
  async handle(route: Route): Promise<boolean> {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/recognition/acknowledgment-audio') { await route.fulfill({status:503,json:{detail:'Optional cue unavailable in this exercise fixture'}}); return true; }
    if (!path.startsWith('/api/lessons/')) return false;
    // Empty restoration reads need no browser clock (the page may be reloading).
    if (path.endsWith('/current') && !this.value) {
      await route.fulfill({json:null}); return true;
    }
    const now = await this.page.evaluate(() => Date.now());
    if (this.value?.status === 'active') this.value.elapsed_seconds = Math.max(0,(now-this.anchor)/1000);
    if (path.endsWith('/start') && (!this.value || this.value.status === 'completed')) {
      const fresh = this.value?.status === 'completed'; this.anchor=now;
      this.value={id:'lesson',revision:0,status:'active',phase:'EXERCISE',prompt:null,pending:null,
        exercise:fresh?null:this.current(),elapsed_seconds:0,remaining_seconds:600,server_time:new Date(now).toISOString(),
        focus:'details',exercises_completed:0,strength:null,improvement_focus:null,encouragement:'Thanks for listening, Robert.'};
    }
    if (path.endsWith('/current')) { await route.fulfill({json:this.value?.status==='completed'?null:this.value});return true; }
    if (!this.value) { await route.fulfill({status:404,json:{detail:'Lesson not found'}});return true; }
    if (path.endsWith('/end')) this.value.status='paused';
    if (path.endsWith('/resume')) {this.value.status='active';this.anchor=now-this.value.elapsed_seconds*1000;}
    if (path.endsWith('/exercise')) {this.value.exercise=this.generate();this.value.revision++;}
    if (path.endsWith('/advance')) {
      this.value.exercises_completed++;this.value.exercise=null;this.value.revision++;
      if (this.value.elapsed_seconds>420) {this.value.phase='CLOSING';this.value.prompt='Today, you caught the main idea. Listen for dates and times next time. Thanks for listening together.';this.value.strength='following the main idea';}
    }
    if (path.endsWith('/audio')) { const {promptWav}=await import('./voice-fixture');await route.fulfill({contentType:'audio/wav',body:promptWav()});return true; }
    if (path.endsWith('/heard')) {this.value.phase='COMPLETED';this.value.status='completed';this.value.revision++;}
    this.value.remaining_seconds=Math.max(0,600-this.value.elapsed_seconds);
    await route.fulfill({json:{...this.value,exercise:this.value.exercise?this.current():null}});return true;
  }
}
