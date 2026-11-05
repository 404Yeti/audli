import { AuthGate } from '../../../components/auth-gate';
import { ProgressPage } from '../../../components/listening/ProgressPage';

export default function Page() { return <AuthGate><ProgressPage /></AuthGate>; }
