import { Navigate, useLocation } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { useAuth } from "./AuthContext";

interface Props {
  children: React.ReactNode;
}

export function ProtectedRoute({ children }: Props) {
  const { user, loading, connectionError, refresh } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-base-900 text-warm-300">
        <Loader2 className="h-5 w-5 animate-spin" strokeWidth={1.5} />
      </div>
    );
  }
  if (!user && connectionError) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-base-900 text-warm-300">
        <p className="text-sm tracking-wide">连接服务器失败，请检查网络后重试。</p>
        <button type="button" className="btn-gold" onClick={() => void refresh()}>
          重试
        </button>
      </div>
    );
  }
  if (!user) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }
  return <>{children}</>;
}
