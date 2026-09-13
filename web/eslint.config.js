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

// The one exception, and it is the same one the Python side makes: the cat that
// has sat at the top of this project's entry points since v1 is drawn in
// katakana and half-width forms, so a blanket rule rejects it.
//
// Exempted by SHAPE, not by filename or by a disable comment - a line qualifies
// only if every character on it comes from the mark's own alphabet, so it cannot
// become somewhere to park a Chinese sentence.
//
// Split in two because only the first half can be shared. The glyphs the cat is
// drawn from are the same in both languages and
// `tests/unit/test_repo_hygiene.py` asserts this list and the one in
// `tests/support/marks.py` are identical. What differs is the syntax around
// them: a Python line starts with `#`, a TypeScript one is a quoted string in
// an array.
const MARK_DRAWING = [
  '\u2502',
  '\u2b50',
  '\ufe0f',
  '\u3000',
  '\u3064',
  '\u30ce',
  '\u30d5',
  '\u30df',
  '\u30fd',
  '\u4e8c',
  '\uff09',
  '\uff0f',
  '\uff1e',
  '\uff3c',
  '\uff3f',
  '\uff89',
  '\uffe3',
]

//: ASCII the mark uses, plus what TypeScript wraps it in: quotes, a comma, and
//: the `#` a Python comment would have started with.
const MARK_SYNTAX = " \t#'(),/=FS_`adelmrstx|"

const MARK_GLYPHS = new Set([...MARK_DRAWING, ...MARK_SYNTAX])

/** Whether the line this character sits on is the mark rather than prose. */
const onMarkLine = (text, index) => {
  const start = text.lastIndexOf('\n', index) + 1
  const lineEnd = text.indexOf('\n', index)
  const line = text.slice(start, lineEnd === -1 ? text.length : lineEnd)
  return line.trim().length > 0 && [...line].every((char) => MARK_GLYPHS.has(char))
}

// Attributes that reach a human's eyes. Anything else (className, id, href,
// data-*, aria-controls) is machinery and stays a literal.
const TRANSLATABLE_ATTRS = new Set([
  'alt',
  'aria-label',
  'aria-description',
  'aria-placeholder',
  'aria-roledescription',
  'caption',
  'cardLabel',
  'confirmLabel',
  'cancelLabel',
  'description',
  'emptyText',
  'header',
  'heading',
  'hint',
  'label',
  'placeholder',
  'message',
  'subtitle',
  'summary',
  'tooltip',
  'title',
])

// Text that is not prose even though it is made of letters: identifiers the user
// must be able to copy verbatim, units, and the product name. Translating any of
// these would be a bug, not a fix.
const NOT_PROSE = [
  /^[^A-Za-z]*$/, //           punctuation, digits, arrows, currency
  /^[A-Z0-9_]+$/, //           SCREAMING_CASE constants and error codes
  /^[a-z0-9]+([._-][a-z0-9]+)+$/, // setting keys, endpoint names, mime types
  /^(dtk|DTK|API|MCP|URL|UA|TLS|JSON|YAML|CSV|HTTP|HTTPS|ID|IDs|UTC|px|ms|s|m|h|d|KB|MB|GB|QPS|TTL)$/,
  /^v?\d+(\.\d+)*$/, //       version numbers
  /^[A-Za-z]$/, //             single letters
  /:\/\//, //                  URLs and connection templates
  /^[a-z_]+=/, //              key=value examples, e.g. cookie strings
  /^[A-Z][a-z]+\/[A-Za-z_]+$/, // IANA time zones, e.g. Asia/Shanghai
  /^[A-Za-z0-9+/=_-]{10,}$/, // opaque ids and base64-ish handles
]

// The design system already marks machine text: a mono class, or one of the
// HTML elements that mean "verbatim". Text inside those is a value the user has
// to be able to read and copy exactly, so translating it would be the bug.
const VERBATIM_ELEMENTS = new Set(['code', 'pre', 'kbd', 'samp'])
const MONO_CLASS = /(^|\s)(u-mono|mono)(\s|$)/

const isVerbatimHost = (node) => {
  for (let current = node; current; current = current.parent) {
    if (current.type !== 'JSXElement') continue
    const opening = current.openingElement
    const name = opening.name.type === 'JSXIdentifier' ? opening.name.name : ''
    if (VERBATIM_ELEMENTS.has(name)) return true
    for (const attribute of opening.attributes) {
      if (attribute.type !== 'JSXAttribute') continue
      const attributeName =
        attribute.name.type === 'JSXIdentifier' ? attribute.name.name : ''
      if (attributeName !== 'className') continue
      const value = attribute.value
      if (value?.type === 'Literal' && MONO_CLASS.test(String(value.value))) return true
    }
  }
  return false
}

const isProse = (raw) => {
  const text = raw.trim()
  if (text.length < 2) return false
  // Needs at least two consecutive letters somewhere to be a word at all.
  if (!/[A-Za-z]{2}/.test(text)) return false
  return !NOT_PROSE.some((pattern) => pattern.test(text))
}

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
    'no-untranslated-text': {
      meta: {
        type: 'problem',
        docs: {
          description:
            'Disallow user-visible literal text in JSX; it must come from a translation key.',
        },
        schema: [],
        messages: {
          text: 'Untranslated text "{{value}}". Move it into src/locales/{en,zh}/*.json and render it with t(). If it is an identifier rather than prose, add an eslint-disable-next-line with the reason.',
          attr: 'Untranslated {{name}}="{{value}}". Pass t(\'...\') instead. If it is an identifier rather than prose, add an eslint-disable-next-line with the reason.',
        },
      },
      create(context) {
        const literalValue = (node) => {
          if (!node) return null
          if (node.type === 'Literal' && typeof node.value === 'string') return node.value
          if (node.type === 'JSXExpressionContainer') return literalValue(node.expression)
          if (node.type === 'TemplateLiteral' && node.expressions.length === 0) {
            return node.quasis.map((q) => q.value.cooked).join('')
          }
          return null
        }
        return {
          JSXText(node) {
            if (isProse(node.value) && !isVerbatimHost(node)) {
              context.report({ node, messageId: 'text', data: { value: node.value.trim() } })
            }
          },
          JSXAttribute(node) {
            const name = node.name.type === 'JSXIdentifier' ? node.name.name : null
            if (!name || !TRANSLATABLE_ATTRS.has(name)) return
            const value = literalValue(node.value)
            if (value !== null && isProse(value)) {
              context.report({ node, messageId: 'attr', data: { name, value } })
            }
          },
          // {cond ? 'Yes' : 'No'} and {'Saved'} render text without ever being
          // an attribute, so the attribute check alone cannot see them.
          JSXExpressionContainer(node) {
            if (node.parent?.type !== 'JSXElement' && node.parent?.type !== 'JSXFragment') return
            const strings = []
            const collect = (expression) => {
              if (!expression) return
              if (expression.type === 'ConditionalExpression') {
                collect(expression.consequent)
                collect(expression.alternate)
                return
              }
              if (expression.type === 'LogicalExpression') {
                collect(expression.right)
                return
              }
              const value = literalValue(expression)
              if (value !== null && isProse(value)) strings.push(value)
            }
            collect(node.expression)
            if (isVerbatimHost(node)) return
            for (const value of strings) {
              context.report({ node, messageId: 'text', data: { value } })
            }
          },
          // Column and option tables are declared as object literals far from
          // the JSX that renders them; the label never passes through an
          // attribute the checks above would see.
          Property(node) {
            const key =
              node.key.type === 'Identifier'
                ? node.key.name
                : node.key.type === 'Literal'
                  ? String(node.key.value)
                  : null
            if (!key || !TRANSLATABLE_ATTRS.has(key)) return
            const value = literalValue(node.value)
            if (value !== null && isProse(value)) {
              context.report({ node, messageId: 'attr', data: { name: key, value } })
            }
          },
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
              if (onMarkLine(text, match.index)) continue
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
    plugins: { local, 'react-hooks': reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // Scoped to the console's own sources: this is about what a user reads on
      // screen. Tooling config elsewhere in the repo carries English strings
      // that are documentation for developers, not copy.
      'local/no-untranslated-text': 'error',
    },
  },
  {
    files: ['vite.config.ts', 'eslint.config.js', 'scripts/**/*.mjs'],
    languageOptions: { globals: { ...globals.node } },
    rules: { 'no-console': 'off' },
  },
)
