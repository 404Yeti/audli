/** Audli 2.0 pages exist only where this is switched on at build/start time (staging, local development). Everywhere else, including production,
 *  /listening answers 404, so a merge to main can never put unfinished 2.0 screens in front of learners. The backend has its own flag
 *  (AUDLI_LISTENING_ENABLED, off by default); both must be on for the feature to work. */
export const listeningEnabled = (env: Record<string, string | undefined> = process.env): boolean => env.NEXT_PUBLIC_AUDLI_LISTENING === 'true';
