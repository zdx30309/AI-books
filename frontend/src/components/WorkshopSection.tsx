import { Video, Wand2, ImageIcon, Upload } from "lucide-react";
import { FeatureCard } from "./FeatureCard";

interface Props {
  onStartCreate: () => void;
}

export function WorkshopSection({ onStartCreate }: Props) {
  return (
    <section className="mt-16">
      <header className="mb-6 flex items-center justify-between">
        <h2 className="flex items-center gap-3 font-serif text-xl tracking-widest text-warm-100">
          <span className="h-4 w-[3px] rounded-full bg-gradient-to-b from-gold-300 to-gold-600" />
          工作台
        </h2>
      </header>

      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-4">
        <FeatureCard
          icon={Video}
          title="视频转书稿"
          description="AI 提取视频文稿，智能优化内容结构"
          onClick={onStartCreate}
        />
        <FeatureCard
          icon={Wand2}
          title="智能润色"
          description="优化语言表达，提升阅读体验"
          comingSoon
        />
        <FeatureCard
          icon={ImageIcon}
          title="封面生成"
          description="AI 生成高质量书籍封面，多种风格可选"
          comingSoon
        />
        <FeatureCard
          icon={Upload}
          title="导出发布"
          description="一键导出多种格式，轻松发布与分享"
          comingSoon
        />
      </div>
    </section>
  );
}
