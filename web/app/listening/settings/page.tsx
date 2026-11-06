import { AuthGate } from '../../../components/auth-gate';
import { SettingsPage } from '../../../components/listening/SettingsPage';

export default function Page() { return <AuthGate><SettingsPage /></AuthGate>; }
