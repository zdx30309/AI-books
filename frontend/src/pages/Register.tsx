import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Loader2, UserPlus } from "lucide-react";
import { useAuth } from "@/auth/AuthContext";
import { ApiError } from "@/api/client";
import { AuthShell, Field } from "./Login";

type Mode = "email" | "phone";

const PHONE_RE = /^1[3-9]\d{9}$/;

export function RegisterPage() {
  const navigate = useNavigate();
  const { register } = useAuth();

  const [mode, setMode] = useState<Mode>("email");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);

    if (mode === "email" && !email.trim()) {
      setError("请输入邮箱");
      return;
    }
    if (mode === "phone" && !PHONE_RE.test(phone.trim())) {
      setError("手机号格式不正确（11 位 1[3-9]xxxxxxxxx）");
      return;
    }
    if (password.length < 6) {
      setError("密码至少 6 位");
      return;
    }
    if (password !== confirm) {
      setError("两次密码不一致");
      return;
    }

    setSubmitting(true);
    try {
      await register({
        email: mode === "email" ? email.trim() : null,
        phone: mode === "phone" ? phone.trim() : null,
        password,
        name: name.trim() || null,
      });
      navigate("/", { replace: true });
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? `[${e.status}] ${e.detail}`
          : e instanceof Error
            ? e.message
            : "注册失败";
      setError(msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AuthShell title="创建账户" subtitle="把第一段视频思想写成你的书">
      {/* tab 切换 */}
      <div className="mb-6 flex gap-2">
        {(
          [
            { v: "email" as Mode, label: "邮箱注册" },
            { v: "phone" as Mode, label: "手机号注册" },
          ]
        ).map(({ v, label }) => (
          <button
            key={v}
            type="button"
            onClick={() => setMode(v)}
            className={`flex-1 rounded-lg border px-3 py-2 text-xs tracking-widest transition-colors ${
              mode === v
                ? "border-gold-400/60 bg-gold-500/10 text-warm-100"
                : "border-gold-500/15 text-warm-400 hover:border-gold-400/30 hover:text-warm-200"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <form onSubmit={onSubmit} className="space-y-5">
        {mode === "email" ? (
          <Field label="邮箱">
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              className="auth-input"
              autoFocus
            />
          </Field>
        ) : (
          <Field label="手机号">
            <input
              type="tel"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="13800138000"
              maxLength={11}
              className="auth-input"
              autoFocus
            />
          </Field>
        )}

        <Field label="昵称（选填）">
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="墨语者"
            maxLength={64}
            className="auth-input"
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

        <Field label="再次输入密码">
          <input
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            placeholder="确认密码"
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
              <span className="tracking-widest">提交中</span>
            </>
          ) : (
            <>
              <UserPlus className="h-4 w-4" strokeWidth={1.8} />
              <span className="tracking-widest">注册并登录</span>
            </>
          )}
        </button>

        <p className="text-center text-xs tracking-wide text-warm-400">
          已经有账号？{" "}
          <Link
            to="/login"
            className="text-gold-300 transition-colors hover:text-gold-200"
          >
            去登录
          </Link>
        </p>
      </form>
    </AuthShell>
  );
}
