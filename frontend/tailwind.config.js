/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // 暗色主基调 — 偏暖的深棕黑
        base: {
          900: "#0a0908",
          800: "#13110f",
          700: "#1a1614",
          600: "#221d1a",
          500: "#2a2421",
        },
        // 琥珀金主题色
        gold: {
          50: "#faf3e6",
          100: "#f0e2c2",
          200: "#e8d09a",
          300: "#dcbc73",
          400: "#d4a874",
          500: "#c4a572",
          600: "#a88959",
          700: "#806840",
        },
        // 暖色文字
        warm: {
          100: "#f5f0e8",
          200: "#e8ddc9",
          300: "#c9b9a1",
          400: "#9b9285",
          500: "#7a7269",
          600: "#5a544c",
        },
      },
      fontFamily: {
        sans: [
          "system-ui",
          "-apple-system",
          "PingFang SC",
          "Microsoft YaHei",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        serif: [
          "Cormorant Garamond",
          "Songti SC",
          "STSong",
          "Source Han Serif SC",
          "serif",
        ],
        cursive: ["Pinyon Script", "Italianno", "Snell Roundhand", "cursive"],
      },
      backgroundImage: {
        "hero-glow":
          "radial-gradient(ellipse 80% 60% at 75% 30%, rgba(212, 168, 116, 0.18), transparent 65%), radial-gradient(ellipse 60% 80% at 90% 10%, rgba(232, 199, 146, 0.12), transparent 60%)",
        "card-gloss":
          "linear-gradient(135deg, rgba(212, 168, 116, 0.06) 0%, rgba(255, 255, 255, 0.02) 50%, rgba(212, 168, 116, 0.04) 100%)",
        "gold-gradient":
          "linear-gradient(135deg, #e8c792 0%, #d4a874 50%, #b88a52 100%)",
      },
      boxShadow: {
        "gold-glow": "0 0 30px rgba(212, 168, 116, 0.25)",
        "gold-soft": "0 4px 20px rgba(212, 168, 116, 0.15)",
        "card-warm": "0 4px 24px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(212, 168, 116, 0.08)",
      },
    },
  },
  plugins: [],
};
