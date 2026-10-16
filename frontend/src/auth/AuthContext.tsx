import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, ApiError, onUnauthorized, tokenStore } from "@/api/client";
import type { LoginRequest, RegisterRequest, UserPublic } from "@/api/types";

interface AuthState {
  user: UserPublic | null;
  loading: boolean;
  /** 有 token 但因网络/服务器错误没能确认登录状态 */
  connectionError: boolean;
  login: (body: LoginRequest) => Promise<void>;
  register: (body: RegisterRequest) => Promise<void>;
  logout: () => void;
  refresh: () => Promise<void>;
}

const AuthCtx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserPublic | null>(null);
  const [loading, setLoading] = useState(true);
  const [connectionError, setConnectionError] = useState(false);

  const refresh = useCallback(async () => {
    if (!tokenStore.get()) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      const me = await api.me();
      setUser(me);
      setConnectionError(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        tokenStore.clear();
        setUser(null);
        setConnectionError(false);
      } else {
        // 网络或服务器错误：token 可能仍有效，不要当成未登录
        setConnectionError(true);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    onUnauthorized(() => setUser(null));
    return () => onUnauthorized(null);
  }, []);

  const login = useCallback(async (body: LoginRequest) => {
    const res = await api.login(body);
    tokenStore.set(res.access_token);
    setUser(res.user);
  }, []);

  const register = useCallback(async (body: RegisterRequest) => {
    const res = await api.register(body);
    tokenStore.set(res.access_token);
    setUser(res.user);
  }, []);

  const logout = useCallback(() => {
    tokenStore.clear();
    setUser(null);
  }, []);

  const value = useMemo<AuthState>(
    () => ({ user, loading, connectionError, login, register, logout, refresh }),
    [user, loading, connectionError, login, register, logout, refresh],
  );

  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth 必须在 AuthProvider 内使用");
  return ctx;
}
