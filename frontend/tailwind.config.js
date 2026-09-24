/** @type {import('tailwindcss').Config} */
import defaultColors from "tailwindcss/colors.js";
import plugin from "tailwindcss/plugin.js";

const themeColor = (token) => `rgb(var(--color-${token}) / <alpha-value>)`;
const scale = (name, shades) =>
  Object.fromEntries(shades.map((shade) => [shade, themeColor(`${name}-${shade}`)]));

// Status colours (red / emerald / amber …) are hard-coded across pages for
// dark surfaces. They resolve through CSS variables so light themes can flip
// the lightness scale (text-emerald-200 becomes emerald-800) and keep contrast
// without touching any page.
const SHADES = ["50", "100", "200", "300", "400", "500", "600", "700", "800", "900", "950"];
const FLIPPED = Object.fromEntries(SHADES.map((shade, index) => [shade, SHADES[SHADES.length - 1 - index]]));
const ADAPTIVE_PALETTES = [
  "slate", "gray", "zinc", "neutral", "stone", "red", "orange", "amber", "yellow", "lime", "green",
  "emerald", "teal", "cyan", "sky", "blue", "indigo", "violet", "purple", "fuchsia", "pink", "rose",
];

const toRgb = (hex) => {
  const value = hex.replace("#", "");
  const full = value.length === 3 ? value.split("").map((c) => c + c).join("") : value;
  const n = Number.parseInt(full, 16);
  return `${(n >> 16) & 255} ${(n >> 8) & 255} ${n & 255}`;
};

const adaptiveColors = Object.fromEntries(
  ADAPTIVE_PALETTES.map((name) => [
    name,
    Object.fromEntries(SHADES.map((shade) => [shade, `rgb(var(--pal-${name}-${shade}) / <alpha-value>)`])),
  ]),
);

const paletteVariables = (flip) =>
  Object.fromEntries(
    ADAPTIVE_PALETTES.flatMap((name) =>
      SHADES.map((shade) => [`--pal-${name}-${shade}`, toRgb(defaultColors[name][flip ? FLIPPED[shade] : shade])]),
    ),
  );

// Pages use opacity steps such as /8, /12 and /18; Tailwind 3 only ships
// multiples of five, so those classes silently produced no CSS.
const opacity = Object.fromEntries(Array.from({ length: 101 }, (_, i) => [String(i), String(i / 100)]));

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ...adaptiveColors,
        base: scale("base", ["500", "600", "700", "800", "900", "950"]),
        gold: scale("gold", ["50", "100", "200", "300", "400", "500", "600", "700", "800", "900", "950"]),
        warm: scale("warm", ["50", "100", "200", "300", "400", "500", "600"]),
      },
      opacity,
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          '"SF Pro Text"',
          '"Segoe UI Variable Text"',
          '"Segoe UI"',
          '"PingFang SC"',
          '"HarmonyOS Sans SC"',
          '"MiSans"',
          '"Microsoft YaHei UI"',
          '"Microsoft YaHei"',
          '"Noto Sans SC"',
          "system-ui",
          "sans-serif",
        ],
        serif: [
          '"Iowan Old Style"',
          '"Palatino Linotype"',
          '"Book Antiqua"',
          "Georgia",
          '"Songti SC"',
          '"STSong"',
          '"Noto Serif SC"',
          '"Source Han Serif SC"',
          '"Source Han Serif"',
          '"SimSun"',
          "serif",
        ],
        cursive: ['"Iowan Old Style"', '"Palatino Linotype"', "Georgia", "serif"],
        mono: ['"SF Mono"', '"JetBrains Mono"', '"Cascadia Mono"', "Consolas", "Menlo", "monospace"],
      },
      backgroundImage: {
        "hero-glow":
          "radial-gradient(ellipse 70% 80% at 88% 0%, rgb(var(--color-gold-400) / 0.16), transparent 62%), radial-gradient(ellipse 45% 60% at 100% 100%, rgb(var(--color-gold-600) / 0.08), transparent 60%)",
        "card-gloss":
          "linear-gradient(180deg, rgb(var(--color-warm-100) / 0.035) 0%, transparent 42%)",
        "gold-gradient":
          "linear-gradient(135deg, rgb(var(--color-gold-200)) 0%, rgb(var(--color-gold-400)) 50%, rgb(var(--color-gold-600)) 100%)",
      },
      boxShadow: {
        "gold-glow": "var(--shadow-gold-glow)",
        "gold-soft": "var(--shadow-gold-soft)",
        "card-warm": "var(--shadow-card-warm)",
        float: "var(--shadow-float)",
      },
      transitionTimingFunction: {
        "out-expo": "cubic-bezier(0.16, 1, 0.3, 1)",
      },
      keyframes: {
        "fade-in-up": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "scale-in": {
          from: { opacity: "0", transform: "translateY(-4px) scale(0.98)" },
          to: { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        shimmer: {
          from: { backgroundPosition: "-200% 0" },
          to: { backgroundPosition: "200% 0" },
        },
      },
      animation: {
        "page-in": "fade-in-up 0.45s cubic-bezier(0.16, 1, 0.3, 1) both",
        "scale-in": "scale-in 0.18s cubic-bezier(0.16, 1, 0.3, 1) both",
        shimmer: "shimmer 2.2s linear infinite",
      },
    },
  },
  plugins: [
    plugin(({ addBase }) => {
      addBase({
        ":root": paletteVariables(false),
        'html[data-mode="light"]': paletteVariables(true),
      });
    }),
  ],
};
