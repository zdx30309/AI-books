export const AIGC_GATE_VISIBLE =
  String(import.meta.env.VITE_AIGC_GATE_ENABLED ?? "false").toLowerCase() === "true";
