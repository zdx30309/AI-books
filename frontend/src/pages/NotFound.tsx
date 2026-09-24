import { Link } from "react-router-dom";
import { ArrowLeft, Compass } from "lucide-react";

export function NotFound() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center text-center">
      <span className="flex h-14 w-14 items-center justify-center rounded-2xl border border-gold-400/20 bg-gold-400/10">
        <Compass className="h-6 w-6 text-gold-300" strokeWidth={1.6} />
      </span>
      <p className="eyebrow mt-6">404</p>
      <h1 className="hero-title mt-3 text-3xl">这一页还没有写出来</h1>
      <p className="mt-3 max-w-sm text-sm leading-relaxed text-warm-400">
        链接可能已经失效，或者页面换了位置。按 Ctrl K 可以搜索任何页面。
      </p>
      <Link to="/" className="btn-gold mt-8">
        <ArrowLeft className="h-4 w-4" strokeWidth={2} />
        回到工作台
      </Link>
    </div>
  );
}
