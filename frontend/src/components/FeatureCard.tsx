import type { LucideIcon } from "lucide-react";

interface Props {
  icon: LucideIcon;
  title: string;
  description: string;
  onClick?: () => void;
  comingSoon?: boolean;
}

export function FeatureCard({
  icon: Icon,
  title,
  description,
  onClick,
  comingSoon = false,
}: Props) {
  const Component = onClick ? "button" : "div";
  return (
    <Component
      type={onClick ? "button" : undefined}
      onClick={onClick}
      className={`card-warm group relative flex w-full flex-col p-6 text-left transition-all ${
        onClick
          ? "cursor-pointer hover:-translate-y-0.5 hover:border-gold-400/30 hover:shadow-gold-soft"
          : ""
      }`}
    >
      <div className="flex-1">
        <h3 className="font-serif text-lg tracking-wide text-warm-100">
          {title}
        </h3>
        <p className="mt-2 text-xs leading-relaxed tracking-wide text-warm-400 line-clamp-2">
          {description}
        </p>
      </div>

      {comingSoon && (
        <span className="mt-3 inline-flex w-fit items-center rounded-full bg-warm-600/20 px-2 py-0.5 text-[10px] tracking-widest text-warm-400">
          即将上线
        </span>
      )}

      {/* 右下角图标球 */}
      <div className="absolute bottom-5 right-5 flex h-12 w-12 items-center justify-center rounded-full border border-gold-500/20 bg-base-700/80 shadow-gold-soft transition-transform duration-300 group-hover:scale-110">
        <Icon className="h-5 w-5 text-gold-300" strokeWidth={1.5} />
      </div>
    </Component>
  );
}
