/** The reversible switch that makes Audli 2.0 the landing experience. Both flags are read when the web app builds/starts and both default to off:
 *  NEXT_PUBLIC_AUDLI_LISTENING=true       the 2.0 pages exist (otherwise /listening answers 404)
 *  NEXT_PUBLIC_AUDLI_LISTENING_HOME=true  and "/" serves the Audli 2.0 home (landing for visitors, Learner Home when signed in) without a redirect;
 *                                         the classic 1.x home stays reachable at /classic
 *  Rollback is unsetting either variable and redeploying: nothing is deleted and 1.x was never changed. */
type Env = Record<string, string | undefined>;
export const cutoverEnabled = (env: Env = process.env): boolean => env.NEXT_PUBLIC_AUDLI_LISTENING === 'true' && env.NEXT_PUBLIC_AUDLI_LISTENING_HOME === 'true';
/** No redirect any more: "/" is rewritten (before the filesystem is checked, because the 1.x page lives at "/") to the 2.0 home, so the address stays "/". */
export const cutoverRedirects = (): never[] => [];
export const cutoverBeforeFiles = (env: Env = process.env) => (cutoverEnabled(env) ? [{ source: '/', destination: '/home' }] : []);
export const cutoverAfterFiles = (env: Env = process.env) => (cutoverEnabled(env) ? [{ source: '/classic', destination: '/' }] : []);
export const cutoverRewrites = cutoverAfterFiles;
