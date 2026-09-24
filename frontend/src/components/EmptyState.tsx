import { BookOpen } from "lucide-react";

interface Props {
  title: string;
  description: string;
  action?: React.ReactNode;
}

export function EmptyState({ title, description, action }: Props) {
  return (
    <div className="card-warm flex flex-col items-center justify-center px-8 py-16 text-center">
      <div className="relative mb-6">
        <div className="absolute inset-0 -z-0 scale-150 rounded-full bg-gold-400/10 blur-2xl" />
        <div className="relative flex h-14 w-14 items-center justify-center rounded-2xl border border-gold-400/20 bg-gold-400/10">
          <BookOpen className="h-6 w-6 text-gold-300" strokeWidth={1.5} />
        </div>
      </div>
      <h3 className="font-serif text-xl text-warm-50">{title}</h3>
      <p className="mt-2 max-w-md text-[13px] leading-relaxed text-warm-400">{description}</p>
      {action && <div className="mt-7">{action}</div>}
    </div>
  );
}
