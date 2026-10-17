import type { TaskCategoryOption } from "@/api/types";

export type BookshelfVersion = "1.0" | "2.0";
export const V2_CATEGORY_IDS = new Set(["social_science_test", "social_pain_2610"]);
export function categoriesForShelf(items: TaskCategoryOption[], version: BookshelfVersion) {
  return items.filter((item) => V2_CATEGORY_IDS.has(item.id) === (version === "2.0"));
}
export function taskShelfVersion(task: { bookshelf_version?: string | null; text_workflow_version?: string | null }): BookshelfVersion {
  return task.bookshelf_version === "2.0" || (!task.bookshelf_version && ["sheke2610_astra_pro_20261016", "social_pain2610_classic_astra_20261017"].includes(task.text_workflow_version ?? "")) ? "2.0" : "1.0";
}
export function bookshelfPath(version: BookshelfVersion) {
  return version === "2.0" ? "/bookshelf/v2" : "/bookshelf";
}
export function bookPath(id: string, version: BookshelfVersion) {
  return version === "2.0" ? `/book/v2/${id}` : `/book/${id}`;
}
