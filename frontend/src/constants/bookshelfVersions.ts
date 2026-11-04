import type { TaskCategoryOption } from "@/api/types";

export type BookshelfVersion = "1.0" | "2.0";
export const ASTRA_DIRECT_VERSIONS = ["general_manuscript_astra_direct_20261105", "social_pain2611_astra_direct_20261102"];
export const ASTRA_AGENT_VERSIONS = ["health2610_classic_astra_20261104v2", "health2610_classic_astra_20261104", "health2610_classic_astra_20261103", "social_pain2610_2_classic_astra_20261102", "psychology2610_classic_astra_20261102", "health2610_classic_astra_20261102", "social_pain2610_2_classic_astra_20261029", "psychology2610_classic_astra_20261029", "health2610_classic_astra_20261029", "finance2610_classic_astra_20261028", "psychology2610_classic_astra_20261028", "health2610_classic_astra_20261028", "finance2610_classic_astra_20261027", "psychology2610_classic_astra_20261027", "health2610_classic_astra_20261027"];
export const V2_CATEGORY_IDS = new Set(["general_manuscript", "social_pain_2611_astra_direct", "social_science_test", "social_pain_2610", "social_pain_2610_2", "finance_technology_2610", "psychology_emotion_2610", "health_wellness_2610"]);
export function categoriesForShelf(items: TaskCategoryOption[], version: BookshelfVersion) {
  return items.filter((item) => V2_CATEGORY_IDS.has(item.id) === (version === "2.0"));
}
export function taskShelfVersion(task: { bookshelf_version?: string | null; text_workflow_version?: string | null }): BookshelfVersion {
  return task.bookshelf_version === "2.0" || (!task.bookshelf_version && ["sheke2610_astra_pro_20261016", "social_pain2610_classic_astra_20261017", ...ASTRA_AGENT_VERSIONS, ...ASTRA_DIRECT_VERSIONS].includes(task.text_workflow_version ?? "")) ? "2.0" : "1.0";
}
export function bookshelfPath(version: BookshelfVersion) {
  return version === "2.0" ? "/bookshelf/v2" : "/bookshelf";
}
export function bookPath(id: string, version: BookshelfVersion) {
  return version === "2.0" ? `/book/v2/${id}` : `/book/${id}`;
}
