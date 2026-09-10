import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";

const eslintConfig = [
  ...coreWebVitals,
  ...typescript,
  {
    // eslint-config-next 16 pulls eslint-plugin-react-hooks v7, whose new
    // compiler-powered diagnostics flag long-standing mount-effect data loads
    // (set-state-in-effect) and the GSAP contextSafe closure pattern (refs)
    // across existing components. Downgraded to warnings for the Next 16
    // security upgrade (M14-05) instead of refactoring 10 business files;
    // follow-up debt: migrate those call sites to the v7 guidance.
    rules: {
      "react-hooks/refs": "warn",
      "react-hooks/set-state-in-effect": "warn",
    },
  },
  {
    ignores: [".next/**", "node_modules/**", "next-env.d.ts"],
  },
];

export default eslintConfig;
