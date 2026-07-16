import eslint from "@eslint/js";
import globals from "globals";
import pluginVue from "eslint-plugin-vue";
import tseslint from "typescript-eslint";
import vueParser from "vue-eslint-parser";

export default tseslint.config(
  { ignores: ["dist", "node_modules"] },
  eslint.configs.recommended,
  ...pluginVue.configs["flat/recommended"],
  ...tseslint.configs.recommended,
  {
    files: ["**/*.vue"],
    languageOptions: {
      globals: globals.browser,
      parser: vueParser,
      parserOptions: {
        parser: tseslint.parser,
        extraFileExtensions: [".vue"],
      },
    },
    rules: {
      "vue/max-attributes-per-line": "off",
      "vue/singleline-html-element-content-newline": "off",
      "vue/html-self-closing": "off",
    },
  },
);
