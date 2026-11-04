import { AuthGate } from '../../components/auth-gate';
import { ListeningCatalog } from '../../components/listening/ListeningCatalog';

export default function ListeningPage() { return <AuthGate><ListeningCatalog /></AuthGate>; }
