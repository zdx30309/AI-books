import type { LucideIcon } from "lucide-react";
import { ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";

interface Props {
  icon: LucideIcon;
  title: string;
  description: string;
  onClick?: () => void;
  to?: string;
  accent?: boolean;
  comingSoon?: boolean;
}

export function FeatureCard({ icon: Icon, title, description, onClick, to, accent = false, comingSoon = false }: Props) {
  const interactive = Boolean(onClick || to);
  const className = `card-warm group relative flex h-full w-full flex-col gap-4 overflow-hidden p-5 text-left transition duration-300 ease-out-expo ${
    interactive ? "cursor-pointer hover:-translate-y-0.5 hover:border-gold-400/30 hover:shadow-gold-soft" : "opacity-80"
  } ${accent ? "border-gold-400/25" : ""}`;

  const body = (
    <>
      {accent && <div className="pointer-events-none absolute inset-0 bg-hero-glow opacity-70" />}
      <div className="relative flex items-start justify-between">
        <span
          className={`flex h-10 w-10 items-center justify-center rounded-xl border transition-colors duration-300 ${
            accent
              ? "border-gold-400/35 bg-gold-400/15 text-gold-200"
              : "border-warm-100/[0.08] bg-warm-100/[0.03] text-gold-300 group-hover:border-gold-400/30 group-hover:bg-gold-400/10"
          }`}
        >
          <Icon className="h-[18px] w-[18px]" strokeWidth={1.6} />
        </span>
        {interactive && (
          <ArrowUpRight
            className="h-4 w-4 text-warm-500 opacity-0 transition duration-300 group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-gold-300 group-hover:opacity-100"
            strokeWidth={1.8}
          />
        )}
      </div>
      <div className="relative">
        <h3 className="text-[15px] font-medium text-warm-50">{title}</h3>
        <p className="mt-1.5 line-clamp-2 text-[12.5px] leading-relaxed text-warm-400">{description}</p>
      </div>
      {comingSoon && (
        <span className="relative inline-flex w-fit items-center rounded-full bg-warm-600/20 px-2 py-0.5 text-[10px] tracking-widest text-warm-400">
          即将上线
        </span>
      )}
    </>
  );

  if (to) {
    return (
      <Link to={to} className={className}>
        {body}
      </Link>
    );
  }
  if (onClick) {
    return (
      <button type="button" onClick={onClick} className={className}>
        {body}
      </button>
    );
  }
  return <div className={className}>{body}</div>;
}
