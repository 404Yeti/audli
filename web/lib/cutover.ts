/** The reversible switch that makes Audli 2.0 the landing experience. Both flags are read when the web app builds/starts and both default to off:
 *  NEXT_PUBLIC_AUDLI_LISTENING=true       the 2.0 pages exist (otherwise /listening answers 404)
 *  NEXT_PUBLIC_AUDLI_LISTENING_HOME=true  and "/" sends visitors to /listening; the classic 1.x home stays reachable at /classic
 *  Rollback is unsetting either variable and redeploying: nothing is deleted and 1.x was never changed. */
type Env = Record<string, string | undefined>;
export const cutoverEnabled = (env: Env = process.env): boolean => env.NEXT_PUBLIC_AUDLI_LISTENING === 'true' && env.NEXT_PUBLIC_AUDLI_LISTENING_HOME === 'true';
export const cutoverRedirects = (env: Env = process.env) => (cutoverEnabled(env) ? [{ source: '/', destination: '/listening', permanent: false }] : []);
export const cutoverRewrites = (env: Env = process.env) => (cutoverEnabled(env) ? [{ source: '/classic', destination: '/' }] : []);
