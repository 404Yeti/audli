import { defineConfig, globalIgnores } from 'eslint/config';
import react from 'eslint-plugin-react';
import hooks from 'eslint-plugin-react-hooks';
import imports from 'eslint-plugin-import';
import a11y from 'eslint-plugin-jsx-a11y';
import ts from 'typescript-eslint';
import globals from 'globals';

// Keep Next's React/hooks/accessibility/import coverage without its glob dependency.
const base = {
  files: ['**/*.{js,jsx,mjs,ts,tsx,mts,cts}'],
  plugins: { react, 'react-hooks': hooks, import: imports, 'jsx-a11y': a11y },
  languageOptions: {
    parser: ts.parser,
    parserOptions: { ecmaFeatures: { jsx: true }, sourceType: 'module' },
    globals: { ...globals.browser, ...globals.node },
  },
  settings: { react: { version: 'detect' } },
  rules: {
    ...react.configs.recommended.rules,
    ...hooks.configs.recommended.rules,
    'react/no-unknown-property': 'off',
    'react/react-in-jsx-scope': 'off',
    'react/prop-types': 'off',
    'react/jsx-no-target-blank': 'off',
    'import/no-anonymous-default-export': 'warn',
    'jsx-a11y/alt-text': ['warn', { elements: ['img'], img: ['Image'] }],
    'jsx-a11y/aria-props': 'warn',
    'jsx-a11y/aria-proptypes': 'warn',
    'jsx-a11y/aria-unsupported-elements': 'warn',
    'jsx-a11y/role-has-required-aria-props': 'warn',
    'jsx-a11y/role-supports-aria-props': 'warn',
    // Oxlint supplies the other Next rules. This supported ESLint rule covers
    // Next's navigation rule, including computed properties. It deliberately
    // prohibits external destinations too; use Next's routing APIs instead.
    'no-restricted-syntax': ['warn', {
      selector: 'CallExpression > MemberExpression.callee:matches([computed=false][property.name="assign"], [computed=true][property.value="assign"]):has(> :matches(Identifier.object[name="location"], MemberExpression.object:matches([computed=false][property.name="location"], [computed=true][property.value="location"])))',
      message: 'Use Next.js redirect() or useRouter().push() instead of location.assign().',
    }, {
      selector: 'AssignmentExpression > MemberExpression.left:matches([computed=false][property.name="href"], [computed=true][property.value="href"]):has(> :matches(Identifier.object[name="location"], MemberExpression.object:matches([computed=false][property.name="location"], [computed=true][property.value="location"])))',
      message: 'Use Next.js redirect() or useRouter().push() instead of assigning location.href.',
    }],
  },
};

export default defineConfig([
  base,
  ...ts.configs.recommended.map(config => ({ ...config, files: ['**/*.ts', '**/*.tsx'] })),
  { files: ['**/*.ts', '**/*.tsx'], rules: { '@typescript-eslint/no-unused-vars': 'warn', '@typescript-eslint/no-unused-expressions': 'warn' } },
  globalIgnores(['.next/**', '.next-browser-tests/**', 'next-env.d.ts', 'playwright-report/**', 'test-results/**', 'out/**', 'build/**']),
]);
