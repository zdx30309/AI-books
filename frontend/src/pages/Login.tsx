import { FormEvent, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Loader2, LogIn } from "lucide-react";
import { useAuth } from "@/auth/AuthContext";
import { ApiError } from "@/api/client";

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { login } = useAuth();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!identifier.trim() || !password) {
      setError("请输入账号和密码");
      return;
    }
    setSubmitting(true);
    try {
      await login({ identifier: identifier.trim(), password });
      const from = (location.state as { from?: string } | null)?.from ?? "/";
      navigate(from, { replace: true });
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? `[${e.status}] ${e.detail}`
          : e instanceof Error
            ? e.message
            : "登录失败";
      setError(msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AuthShell title="登录" subtitle="让视频思想，成为你的书">
      <form onSubmit={onSubmit} className="space-y-5">
        <Field label="邮箱 / 手机号">
          <input
            type="text"
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            placeholder="you@example.com 或 13800138000"
            className="auth-input"
            autoFocus
          />
        </Field>
        <Field label="密码">
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="6-64 位"
            className="auth-input"
          />
        </Field>

        {error && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/8 px-4 py-2.5 text-xs text-red-300">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={submitting}
          className="btn-gold w-full"
        >
          {submitting ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2} />
              <span className="tracking-widest">登录中</span>
            </>
          ) : (
            <>
              <LogIn className="h-4 w-4" strokeWidth={1.8} />
              <span className="tracking-widest">登录</span>
            </>
          )}
        </button>

        <p className="text-center text-xs tracking-wide text-warm-400">
          还没有账号？{" "}
          <Link
            to="/register"
            className="text-gold-300 transition-colors hover:text-gold-200"
          >
            立即注册
          </Link>
        </p>
      </form>
    </AuthShell>
  );
}

// ─────────────────────────────────────────
// 共享：登录 / 注册 页的外壳
// ─────────────────────────────────────────

export function AuthShell({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <div className="relative flex min-h-screen items-center justify-center bg-base-900 px-6">
      {/* 暖光晕 */}
      <div className="pointer-events-none absolute inset-0 bg-hero-glow" />

      <div className="relative z-10 w-[min(440px,100%)]">
        {/* Logo */}
        <div className="mb-10 text-center">
          <h1 className="font-serif text-3xl tracking-wide text-warm-100">
            AI <span className="text-gold-300">ScriptBook</span>
          </h1>
          <p className="mt-2 text-[11px] tracking-widest text-warm-400">
            {subtitle}
          </p>
        </div>

        <div className="card-warm relative overflow-hidden p-8 shadow-card-warm">
          <div className="pointer-events-none absolute inset-0 bg-card-gloss opacity-50" />
          <div className="relative">
            <h2 className="mb-7 font-serif text-2xl tracking-wide text-warm-100">
              {title}
            </h2>
            {children}
          </div>
        </div>
      </div>
    </div>
  );
}

export function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-2 block text-[11px] tracking-widest text-warm-300">
        {label}
      </span>
      {children}
    </label>
  );
}
