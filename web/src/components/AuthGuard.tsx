import { useState, useCallback, useEffect } from 'react';
import { getAuthToken, UNAUTHORIZED_EVENT } from '../api/client';
import { Login } from '../pages/Login';
import type { ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

export function AuthGuard({ children }: Props) {
  const [authed, setAuthed] = useState(() => !!getAuthToken());

  // 后端返回 401（token 失效/过期）时自动回到登录页
  useEffect(() => {
    const onUnauthorized = () => setAuthed(false);
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, []);

  const handleLogin = useCallback(() => {
    setAuthed(true);
  }, []);

  if (!authed) {
    return <Login onLogin={handleLogin} />;
  }

  return <>{children}</>;
}
