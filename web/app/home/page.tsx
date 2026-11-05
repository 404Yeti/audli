'use client';
import { AuthGate } from '../../components/auth-gate';
import { Landing } from '../../components/listening/Landing';
import { LearnerHome } from '../../components/listening/LearnerHome';

/** Served at "/" (Figma 01 Landing for visitors, 02 Learner Home once signed in) when the cutover is on; see lib/cutover.ts. */
export default function HomePage() {
  return <AuthGate landingView={({ signUp, signIn }) => <Landing onSignUp={signUp} onSignIn={signIn} />}><LearnerHome /></AuthGate>;
}
