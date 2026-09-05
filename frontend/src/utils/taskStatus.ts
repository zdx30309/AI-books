const INTERNAL_TASK_MARKERS = [
  "celery",
  "docker",
  "worker",
  "redis",
  "quota",
  "billing",
  "api key",
  "paid api",
  "resource_exhausted",
  "rate_limit",
  "429",
  "backoff",
  "cooldown",
  "额度",
  "配额",
  "账单",
  "退避",
  "冷却",
  "上游资源",
  "切换模型",
  "不会切换模型",
  "人工复核",
  "manual_review",
];

const INTERNAL_ERROR_MARKERS = [
  ...INTERNAL_TASK_MARKERS,
  "traceback",
  "exception",
  "provider",
  "请求失败",
  "调用失败",
];

function normalized(value: string | null | undefined): string {
  return String(value || "").trim().toLowerCase();
}

function containsAny(value: string, markers: string[]): boolean {
  return markers.some((marker) => value.includes(marker));
}

/**
 * The API keeps detailed diagnostics for operators, but those diagnostics
 * are not a user-facing workflow description.  Keep this guard in the UI as
 * well because old task rows may still contain the historical wording.
 */
export function isInternalTaskText(value: string | null | undefined): boolean {
  return containsAny(normalized(value), INTERNAL_TASK_MARKERS);
}

export function publicTaskStep(
  value: string | null | undefined,
  status?: string | null,
  progress = 0,
): string {
  const raw = String(value || "").trim();
  const lower = normalized(raw);
  const normalizedStatus = normalized(status);

  if (!raw) {
    if (normalizedStatus === "completed") return "已完成";
    if (normalizedStatus === "paused") return "已暂停";
    if (normalizedStatus === "failed") return "暂时中断";
    return progress > 0 ? "正在继续生成" : "排队中";
  }

  if (containsAny(lower, INTERNAL_TASK_MARKERS)) {
    if (normalizedStatus === "paused") return "已暂停";
    if (normalizedStatus === "failed") return "暂时中断";
    return progress > 0 ? "正在继续生成" : "排队中";
  }

  // Remove provider names from legacy rows while retaining the useful stage.
  if (lower.includes("初稿") || lower.includes("writer")) return "正在生成初稿";
  if (lower.includes("一润") || lower.includes("polish")) return "正在进行第一轮润色";
  if (lower.includes("二润") || lower.includes("refine")) return "正在进行第二轮润色";
  if (lower.includes("终稿") || lower.includes("format")) return "正在整理终稿";
  if (lower.includes("质量") || lower.includes("aigc")) return "正在进行质量检查";
  if (lower.includes("获取视频") || lower.includes("解析视频") || lower.includes("文案")) {
    return "正在准备原始内容";
  }

  return raw
    .replace(/openai/gi, "")
    .replace(/gemini/gi, "")
    .replace(/deepseek/gi, "")
    .replace(/朱雀/g, "质量")
    .replace(/\s{2,}/g, " ")
    .trim() || "正在继续生成";
}

export function publicTaskError(value: string | null | undefined): string | null {
  const raw = String(value || "").trim();
  if (!raw) return null;
  if (containsAny(normalized(raw), INTERNAL_ERROR_MARKERS)) return null;
  return raw;
}

export function publicStatusLabel(status: string | null | undefined, progress = 0): string {
  switch (normalized(status)) {
    case "completed":
      return "已完成";
    case "failed":
      return "暂时中断";
    case "paused":
      return "已暂停";
    case "manual_review":
      return "处理中";
    default:
      return progress > 0 ? `生成中 ${progress}%` : "排队中";
  }
}
