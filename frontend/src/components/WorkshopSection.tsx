import { BookOpen, Castle, FileDown, Images, Languages, ScrollText, ShieldCheck, Sparkles } from "lucide-react";
import { FeatureCard } from "./FeatureCard";
import { SectionHeader } from "./BookshelfSection";

interface Props {
  onStartCreate: () => void;
  latestTaskId?: string | null;
}

export function WorkshopSection({ onStartCreate, latestTaskId }: Props) {
  return (
    <section className="mt-14">
      <SectionHeader title="快速开始" description="常用的创作与工具入口" />

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <FeatureCard
          icon={Sparkles}
          title="一键成书"
          description="粘贴视频链接，提取文稿、智能润色、自动封面，全流程一键完成"
          onClick={onStartCreate}
          accent
        />
        <FeatureCard
          icon={FileDown}
          title="导出预览"
          description={
            latestTaskId
              ? "进入最近一本书稿，查看终稿、按段落润色、导出 Markdown / Word"
              : "完成第一本书稿后，可在书架点入查看终稿并导出 Markdown / Word"
          }
          to={latestTaskId ? `/book/${latestTaskId}` : undefined}
        />
        <FeatureCard icon={BookOpen} title="长篇小说" description="设定家族与冲突，串行生成、质检、修复每一章" to="/long-novel" />
        <FeatureCard icon={Castle} title="史诗长篇" description="百万字网文控制台：世界观、大纲、逐章验收" to="/epic-novel" />
        <FeatureCard icon={ScrollText} title="公版融合长篇" description="上传公版书，按模式重写成可出版的长篇" to="/public-domain-rewrite" />
        <FeatureCard icon={Languages} title="公版古文白话" description="文言原文逐段译成自然白话，支持参照译本" to="/public-domain-classical-vernacular" />
        <FeatureCard icon={Images} title="封面书架" description="批量生成出版感封面，按分类参考当当畅销书" to="/covers" />
        <FeatureCard icon={ShieldCheck} title="AI 审稿降率" description="读取朱雀报告，定向改写高风险段落并复检" to="/aigc-review" />
      </div>
    </section>
  );
}
