import { BookOpen } from "lucide-react";

interface Props {
  title: string;
  description: string;
  action?: React.ReactNode;
}

export function EmptyState({ title, description, action }: Props) {
  return (
    <div className="card-warm flex flex-col items-center justify-center px-10 py-16 text-center">
      <div className="mb-5 flex h-14 w-14 items-center justify-center rounded-full border border-gold-500/15 bg-gold-500/5">
        <BookOpen className="h-6 w-6 text-gold-300" strokeWidth={1.5} />
      </div>
      <h3 className="font-serif text-lg tracking-wide text-warm-100">
        {title}
      </h3>
      <p className="mt-2 max-w-md text-xs leading-relaxed tracking-wide text-warm-400">
        {description}
      </p>
      {action && <div className="mt-6">{action}</div>}
    </div>
  );
}
