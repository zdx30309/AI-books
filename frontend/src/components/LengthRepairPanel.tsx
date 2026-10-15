import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { api, ApiError } from "@/api/client";
import type { CreateTaskResponse, LengthRepairResponse } from "@/api/types";

interface LengthRepairPanelProps {
  taskId: string;
  taskContext: string;
  onApproved: (response: CreateTaskResponse) => Promise<void>;
}

function validateRepair(result: LengthRepairResponse): LengthRepairResponse {
  if (
    result.status === "ready" &&
    (typeof result.result_sha256 !== "string" ||
      !/^[a-f0-9]{64}$/i.test(result.result_sha256) ||
      !Number.isSafeInteger(result.chapter_number) ||
      (result.chapter_number ?? 0) < 1 ||
      !Number.isSafeInteger(result.body_words) ||
      (result.body_words ?? 0) < 1)
  ) {
    return { status: "invalid", reason: "服务端未返回完整有效的章节、字数或 SHA-256，不能批准。" };
  }
  if (!["ready", "missing", "invalid"].includes(result.status)) {
    return { status: "invalid", reason: "服务端返回的修复状态无法识别，不能批准。" };
  }
  return result;
}

export function LengthRepairPanel({ taskId, taskContext, onApproved }: LengthRepairPanelProps) {
  const [result, setResult] = useState<LengthRepairResponse | null>(null);
  const [checking, setChecking] = useState(true);
  const [approving, setApproving] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [identified, setIdentified] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const approvingRef = useRef(false);
  const mountedRef = useRef(true);
  const relevant = /篇幅|字数|单章.{0,12}(?:上限|边界|过长|超)|(?:章节|第\s*\d+\s*章).{0,12}(?:过长|超长|压缩)|局部压缩|length[_ -]repair|body_words/i.test(taskContext);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setResult(null);
    setError(null);
    setChecking(true);
    api.getLengthRepair(taskId)
      .then((response) => {
        if (cancelled) return;
        const validated = validateRepair(response);
        if (validated.status !== "missing") setIdentified(true);
        setResult(validated);
      })
      .catch((failure) => {
        if (cancelled) return;
        setError(failure instanceof ApiError && failure.status === 404
          ? "暂未找到局部篇幅修复信息，当前服务可能尚未提供此接口；不能批准或恢复。"
          : `无法核验局部篇幅修复稿：${failure instanceof ApiError ? failure.detail : "请检查网络后重试"}`);
      })
      .finally(() => {
        if (!cancelled) setChecking(false);
      });
    return () => { cancelled = true; };
  }, [taskId, revision]);

  async function approve() {
    if (approvingRef.current || submitted || checking || result?.status !== "ready" || !result.result_sha256) return;
    const hash = result.result_sha256;
    if (!window.confirm(`确认批准第 ${result.chapter_number} 章局部篇幅修复并恢复任务？\n修复稿正文：${result.body_words} 字/词（服务端统计）\nSHA-256：${hash}\n恢复后可能继续调用付费模型并产生费用，仍受原预算及质量门禁限制。`)) return;
    approvingRef.current = true;
    setApproving(true);
    setError(null);
    try {
      const response = await api.approveLengthRepair(taskId, hash);
      if (!mountedRef.current) return;
      setSubmitted(true);
      setResult(null);
      await onApproved(response);
    } catch (failure) {
      if (!mountedRef.current) return;
      setResult(null);
      setError(`批准或恢复未能确认：${failure instanceof ApiError ? failure.detail : "网络异常，提交结果未知"}。请先刷新核验任务与修复稿，不要重复批准。`);
    } finally {
      approvingRef.current = false;
      if (mountedRef.current) setApproving(false);
    }
  }

  if (!relevant && !identified && !submitted) return null;

  return (
    <section className="card-warm mb-8 p-6" aria-label="局部篇幅人工批准">
      <h2 className="font-serif text-xl text-warm-100">局部篇幅人工批准</h2>
      {checking ? <p className="mt-3 text-sm text-warm-400" role="status">正在核验局部压缩稿…</p> : null}
      {submitted ? <p className="mt-3 text-sm text-emerald-300" role="status">已提交批准并恢复任务，正在刷新任务状态；不会重复批准。</p> : null}
      {result?.status === "ready" ? (
        <div className="mt-3 space-y-3 text-sm text-warm-200">
          <p>修复章号：第 {result.chapter_number} 章 · 修复稿正文 {result.body_words} 字/词（服务端统计）</p>
          <p>结果 SHA-256：<code className="break-all select-all">{result.result_sha256}</code></p>
          <p className="text-gold-200">请先核对局部压缩稿。批准后服务端将核验此完整哈希并原子恢复任务；后续可能调用付费模型并产生费用，仍受原预算与质量门禁限制。不会自动批准。</p>
          <button type="button" onClick={() => void approve()} disabled={approving || submitted || checking} className="btn-ghost disabled:opacity-50">
            {approving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            {approving ? "正在核验并恢复" : "确认批准此哈希并恢复（可能产生费用）"}
          </button>
        </div>
      ) : null}
      {result?.status === "missing" ? <p className="mt-3 text-sm text-warm-400">尚无可批准的局部压缩稿。{result.reason || "请先准备并校验局部修复结果，再刷新检查；当前不会恢复或产生批准。"}</p> : null}
      {result?.status === "invalid" ? <p className="mt-3 text-sm text-gold-200">局部压缩稿校验未通过，不能批准。{result.reason || "请检查修复文件及哈希，修正后重新核验。"}</p> : null}
      {error ? <p className="mt-3 text-sm text-red-300" role="alert">{error}</p> : null}
      {!submitted ? <button type="button" onClick={() => setRevision((value) => value + 1)} disabled={checking || approving} className="btn-ghost mt-4 disabled:opacity-50">刷新修复状态</button> : null}
    </section>
  );
}
