/** Browser presentation intent only; never decides server progress or authorization. */
export function conversationIntent(scope: string, kind: 'onboarding' | 'lesson') {
  // New logins change the operation/session suffix. End still applies to the same
  // account in this browser; another account never inherits its presentation intent.
  const key = `audli-ended:${kind}:${scope.split(':')[0]}`;
  return {
    ended() {
      try { return localStorage.getItem(key) === 'true'; }
      catch { return true; } // Storage unavailable: require a gesture rather than stale auto-speech.
    },
    end() { try { localStorage.setItem(key, 'true'); } catch { /* No server checkpoint changes. */ } },
    start() { try { localStorage.removeItem(key); } catch { /* Optional presentation memory. */ } },
  };
}
