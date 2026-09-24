import { FormEvent, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Clapperboard, Layers, Loader2, LogIn, ShieldCheck } from "lucide-react";
import { useAuth } from "@/auth/AuthContext";
import { ApiError } from "@/api/client";
import { BrandMark, BrandWordmark } from "@/components/BrandMark";
import { ThemeSwitcher } from "@/components/ThemeSwitcher";

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

const AUTH_FEATURES = [
  { icon: Clapperboard, title: "任意视频，一键成书", text: "抖音、B 站、YouTube、小红书链接直接转成完整书稿" },
  { icon: Layers, title: "长篇与公版工作台", text: "家族长篇、史诗网文、公版改写、古文白话一站完成" },
  { icon: ShieldCheck, title: "出版级交付", text: "自动封面、AI 审稿降率、Word / Markdown 一键导出" },
];

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
    <div className="relative grid min-h-screen bg-base-900 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
      <ThemeSwitcher variant="floating" />

      {/* 品牌面 */}
      <aside className="relative hidden overflow-hidden border-r border-warm-100/[0.07] bg-base-800/60 lg:flex lg:flex-col lg:justify-between lg:p-14">
        <div className="pointer-events-none absolute inset-0 bg-hero-glow" />
        <svg
          aria-hidden="true"
          viewBox="0 0 600 600"
          className="pointer-events-none absolute -bottom-40 -right-40 h-[720px] w-[720px] text-gold-400/[0.12]"
          fill="none"
          stroke="currentColor"
        >
          {Array.from({ length: 12 }).map((_, i) => (
            <circle key={i} cx="300" cy="300" r={60 + i * 22} strokeWidth="1" />
          ))}
        </svg>

        <div className="relative flex items-center gap-3">
          <BrandMark size={34} />
          <BrandWordmark className="text-xl" />
        </div>

        <div className="relative max-w-lg">
          <p className="eyebrow">AI ScriptBook</p>
          <h1 className="hero-title mt-5 text-[52px] leading-[1.12]">
            让视频思想，
            <br />
            成为你的书
          </h1>
          <ul className="mt-12 space-y-6">
            {AUTH_FEATURES.map(({ icon: Icon, title: featureTitle, text }) => (
              <li key={featureTitle} className="flex gap-4">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-gold-400/20 bg-gold-400/10">
                  <Icon className="h-[18px] w-[18px] text-gold-300" strokeWidth={1.6} />
                </span>
                <span>
                  <span className="block text-[15px] font-medium text-warm-100">{featureTitle}</span>
                  <span className="mt-1 block text-[13px] leading-relaxed text-warm-400">{text}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>

        <p className="relative font-serif text-[15px] italic text-warm-500">在碎片的世界里，把想象写成书。</p>
      </aside>

      {/* 表单面 */}
      <main className="relative flex items-center justify-center px-6 py-16">
        <div className="pointer-events-none absolute inset-0 bg-hero-glow opacity-60 lg:hidden" />
        <div className="relative w-[min(420px,100%)] animate-page-in">
          <div className="mb-10 flex flex-col items-center text-center lg:hidden">
            <BrandMark size={40} />
            <BrandWordmark className="mt-4 text-2xl" />
            <p className="mt-2 text-[12px] tracking-widest text-warm-400">{subtitle}</p>
          </div>

          <h2 className="font-serif text-[30px] leading-tight text-warm-50">{title}</h2>
          <p className="mb-8 mt-2 hidden text-sm text-warm-400 lg:block">{subtitle}</p>
          <div className="mt-6 lg:mt-0">{children}</div>
        </div>
      </main>
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
      <span className="mb-2 block text-[12.5px] font-medium text-warm-300">
        {label}
      </span>
      {children}
    </label>
  );
}
