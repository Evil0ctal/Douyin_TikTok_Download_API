import js from '@eslint/js'
import reactHooks from 'eslint-plugin-react-hooks'
import globals from 'globals'
import tseslint from 'typescript-eslint'

// Bare hex colours are forbidden in source: every colour must come from a token
// in styles/tokens.css (docs/design/12-design-system.md).
const HEX_COLOUR = /#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b/

// CJK anywhere in .ts/.tsx is a bug: comments and identifiers are English only,
// and user-visible Chinese belongs in src/locales/zh/*.json (docs/design/14-i18n.md).
const CJK = /[\u3000-\u303F\u3040-\u30FF\u3400-\u4DBF\u4E00-\u9FFF\uFF00-\uFFEF]/gu

const local = {
  rules: {
    'no-raw-hex-color': {
      meta: {
        type: 'problem',
        docs: { description: 'Disallow literal hex colours; use a design token.' },
        schema: [],
        messages: {
          raw: 'Raw hex colour "{{value}}". Use a var(--token) from styles/tokens.css instead.',
        },
      },
      create(context) {
        const check = (node, value) => {
          if (typeof value === 'string' && HEX_COLOUR.test(value)) {
            context.report({ node, messageId: 'raw', data: { value } })
          }
        }
        return {
          Literal: (node) => check(node, node.value),
          TemplateElement: (node) => check(node, node.value.raw),
        }
      },
    },
    'no-cjk-source': {
      meta: {
        type: 'problem',
        docs: { description: 'Disallow CJK characters in TypeScript sources.' },
        schema: [],
        messages: {
          cjk: 'CJK character "{{char}}" in source. Comments are English only; Chinese text belongs in src/locales/zh/*.json.',
        },
      },
      create(context) {
        return {
          'Program:exit'() {
            const source = context.sourceCode
            const text = source.getText()
            CJK.lastIndex = 0
            let match
            while ((match = CJK.exec(text)) !== null) {
              context.report({
                loc: source.getLocFromIndex(match.index),
                messageId: 'cjk',
                data: { char: match[0] },
              })
            }
          },
        }
      },
    },
  },
}

export default tseslint.config(
  { ignores: ['dist', 'node_modules', 'coverage'] },
  {
    files: ['**/*.{ts,tsx,js,mjs}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2023,
      globals: { ...globals.browser, ...globals.es2023 },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: { local },
    rules: {
      'local/no-raw-hex-color': 'error',
      'local/no-cjk-source': 'error',
      eqeqeq: ['error', 'always', { null: 'ignore' }],
      'no-console': ['error', { allow: ['warn', 'error'] }],
      'no-var': 'error',
      'prefer-const': 'error',
      'object-shorthand': 'error',
      '@typescript-eslint/consistent-type-imports': [
        'error',
        { prefer: 'type-imports', fixStyle: 'inline-type-imports' },
      ],
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_' },
      ],
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-non-null-assertion': 'error',
    },
  },
  {
    files: ['src/**/*.{ts,tsx}'],
    plugins: { 'react-hooks': reactHooks },
    rules: reactHooks.configs.recommended.rules,
  },
  {
    files: ['vite.config.ts', 'eslint.config.js', 'scripts/**/*.mjs'],
    languageOptions: { globals: { ...globals.node } },
    rules: { 'no-console': 'off' },
  },
)
