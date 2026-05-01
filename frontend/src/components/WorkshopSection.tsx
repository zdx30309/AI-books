import { Sparkles, FileDown } from "lucide-react";
import { Link } from "react-router-dom";
import { FeatureCard } from "./FeatureCard";

interface Props {
  onStartCreate: () => void;
  latestTaskId?: string | null;
}

export function WorkshopSection({ onStartCreate, latestTaskId }: Props) {
  return (
    <section className="mt-16">
      <header className="mb-6 flex items-center justify-between">
        <h2 className="flex items-center gap-3 font-serif text-xl tracking-widest text-warm-100">
          <span className="h-4 w-[3px] rounded-full bg-gradient-to-b from-gold-300 to-gold-600" />
          工作台
        </h2>
      </header>

      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
        <FeatureCard
          icon={Sparkles}
          title="一键成书"
          description="抖音视频文稿 → AI 提取 / 智能润色 / 自动封面，全流程一键完成"
          onClick={onStartCreate}
        />

        {latestTaskId ? (
          <Link to={`/book/${latestTaskId}`} className="block">
            <FeatureCard
              icon={FileDown}
              title="导出预览"
              description="进入最近一本书稿，查看终稿、按段落润色、导出 Markdown / 纯文本"
            />
          </Link>
        ) : (
          <FeatureCard
            icon={FileDown}
            title="导出预览"
            description="完成第一本书稿后，可在书架点入查看终稿并导出 Markdown / 纯文本"
          />
        )}
      </div>
    </section>
  );
}
