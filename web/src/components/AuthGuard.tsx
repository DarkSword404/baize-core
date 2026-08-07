import { useState, useCallback } from 'react';
import { getAuthToken } from '../api/client';
import { Login } from '../pages/Login';
import type { ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

export function AuthGuard({ children }: Props) {
  const [authed, setAuthed] = useState(() => !!getAuthToken());

  const handleLogin = useCallback(() => {
    setAuthed(true);
  }, []);

  if (!authed) {
    return <Login onLogin={handleLogin} />;
  }

  return <>{children}</>;
}
