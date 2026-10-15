/** @type {import('tailwindcss').Config} */
const themeColor = (token) => `rgb(var(--color-${token}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Theme colors are resolved from CSS variables so every route can switch
        // themes without rewriting component class names.
        base: {
          900: themeColor("base-900"),
          800: themeColor("base-800"),
          700: themeColor("base-700"),
          600: themeColor("base-600"),
          500: themeColor("base-500"),
        },
        gold: {
          50: themeColor("gold-50"),
          100: themeColor("gold-100"),
          200: themeColor("gold-200"),
          300: themeColor("gold-300"),
          400: themeColor("gold-400"),
          500: themeColor("gold-500"),
          600: themeColor("gold-600"),
          700: themeColor("gold-700"),
        },
        warm: {
          100: themeColor("warm-100"),
          200: themeColor("warm-200"),
          300: themeColor("warm-300"),
          400: themeColor("warm-400"),
          500: themeColor("warm-500"),
          600: themeColor("warm-600"),
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
          "radial-gradient(ellipse 80% 60% at 75% 30%, rgb(var(--color-gold-400) / 0.18), transparent 65%), radial-gradient(ellipse 60% 80% at 90% 10%, rgb(var(--color-gold-200) / 0.12), transparent 60%)",
        "card-gloss":
          "linear-gradient(135deg, rgb(var(--color-gold-400) / 0.06) 0%, rgb(255 255 255 / 0.02) 50%, rgb(var(--color-gold-400) / 0.04) 100%)",
        "gold-gradient":
          "linear-gradient(135deg, rgb(var(--color-gold-200)) 0%, rgb(var(--color-gold-400)) 50%, rgb(var(--color-gold-600)) 100%)",
      },
      boxShadow: {
        "gold-glow": "var(--shadow-gold-glow)",
        "gold-soft": "var(--shadow-gold-soft)",
        "card-warm": "var(--shadow-card-warm)",
      },
    },
  },
  plugins: [],
};
