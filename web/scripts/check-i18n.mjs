/**
 * Locale integrity check (docs/design/14-i18n.md, "CI checks").
 *
 *  1. en and zh must have exactly the same key set. A missing key is a failure,
 *     not a warning: it falls back to English silently and nobody notices.
 *  2. Every message must parse as ICU, in both languages.
 *  3. errors.json must cover every ErrorCode in src/dtk/core/errors.py. The code
 *     enum is the contract; a code with no console copy shows up as a bare
 *     identifier in front of a user.
 */

import { readFileSync, existsSync, readdirSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { IntlMessageFormat } from 'intl-messageformat'

const here = dirname(fileURLToPath(import.meta.url))
const localesDir = resolve(here, '..', 'src', 'locales')
const errorsPy = resolve(here, '..', '..', 'src', 'dtk', 'core', 'errors.py')

const LANGUAGES = ['en', 'zh']
const NAMESPACES = ['common', 'console', 'errors', 'setup']

const problems = []

function flatten(value, prefix, out) {
  for (const [key, entry] of Object.entries(value)) {
    const path = prefix ? `${prefix}.${key}` : key
    if (entry && typeof entry === 'object' && !Array.isArray(entry)) {
      flatten(entry, path, out)
    } else {
      out.set(path, entry)
    }
  }
  return out
}

function load(language, namespace) {
  const file = join(localesDir, language, `${namespace}.json`)
  if (!existsSync(file)) {
    problems.push(`missing locale file: ${file}`)
    return new Map()
  }
  return flatten(JSON.parse(readFileSync(file, 'utf8')), '', new Map())
}

const catalogs = new Map()
for (const language of LANGUAGES) {
  for (const namespace of NAMESPACES) {
    catalogs.set(`${language}:${namespace}`, load(language, namespace))
  }
}

// 1 + 2: parity and ICU syntax.
for (const namespace of NAMESPACES) {
  const [reference, ...others] = LANGUAGES
  const referenceKeys = catalogs.get(`${reference}:${namespace}`)

  for (const language of others) {
    const keys = catalogs.get(`${language}:${namespace}`)
    for (const key of referenceKeys.keys()) {
      if (!keys.has(key)) problems.push(`${language}/${namespace}.json is missing key "${key}"`)
    }
    for (const key of keys.keys()) {
      if (!referenceKeys.has(key)) {
        problems.push(`${language}/${namespace}.json has extra key "${key}" (not in ${reference})`)
      }
    }
  }

  for (const language of LANGUAGES) {
    for (const [key, message] of catalogs.get(`${language}:${namespace}`)) {
      if (typeof message !== 'string') {
        problems.push(`${language}/${namespace}.json key "${key}" is not a string`)
        continue
      }
      try {
        new IntlMessageFormat(message, language)
      } catch (error) {
        problems.push(`${language}/${namespace}.json key "${key}" is not valid ICU: ${error.message}`)
      }
    }
  }
}

// 3: every backend error code has console copy.
if (existsSync(errorsPy)) {
  const source = readFileSync(errorsPy, 'utf8')
  const enumBody = source.split('class ErrorCode(StrEnum):')[1]?.split('\n\n')[0] ?? ''
  const codes = [...enumBody.matchAll(/^\s{4}([A-Z_]+)\s*=/gm)].map((match) => match[1])

  if (codes.length === 0) {
    problems.push('could not parse ErrorCode from src/dtk/core/errors.py')
  }
  for (const language of LANGUAGES) {
    const catalog = catalogs.get(`${language}:errors`)
    for (const code of codes) {
      if (!catalog.has(`code.${code}`)) {
        problems.push(`${language}/errors.json is missing code.${code}`)
      }
      if (!catalog.has(`hint.${code}`)) {
        problems.push(`${language}/errors.json is missing hint.${code}`)
      }
    }
  }

  // src/lib/api.ts keeps its own copy of the enum, and isErrorCode() gates on
  // it: a code missing there is silently rewritten to INTERNAL, so the console
  // shows "an unexpected internal error" for a condition the server explained
  // precisely. Only the catalogue was checked before, which does not catch it.
  const apiTs = resolve(here, '..', 'src', 'lib', 'api.ts')
  if (existsSync(apiTs)) {
    const block = readFileSync(apiTs, 'utf8').match(/export const ERROR_CODES = \[([\s\S]*?)\] as const/)
    if (!block) {
      problems.push('could not find ERROR_CODES in src/lib/api.ts')
    } else {
      const declared = new Set([...block[1].matchAll(/'([A-Z_]+)'/g)].map((m) => m[1]))
      for (const code of codes) {
        if (!declared.has(code)) problems.push(`src/lib/api.ts ERROR_CODES is missing ${code}`)
      }
      for (const code of declared) {
        if (!codes.includes(code)) {
          problems.push(`src/lib/api.ts ERROR_CODES has ${code}, which the API never sends`)
        }
      }
    }
  }
} else {
  console.warn(`check-i18n: ${errorsPy} not found, skipping error-code coverage`)
}

// 4: language names in the switcher are endonyms, identical in every catalogue.
// A reader who cannot read the current language is scanning the picker for the
// name of their own; translating that name is what hides it from them.
{
  const seen = new Map()
  for (const language of LANGUAGES) {
    const catalog = catalogs.get(`${language}:common`)
    for (const named of LANGUAGES) {
      const key = `language.${named}`
      const value = catalog.get(key)
      if (value === undefined) {
        problems.push(`${language}/common.json is missing ${key}`)
        continue
      }
      const previous = seen.get(key)
      if (previous === undefined) {
        seen.set(key, { value, from: language })
      } else if (previous.value !== value) {
        problems.push(
          `${key} differs between catalogues (${previous.from}: ${JSON.stringify(previous.value)}, ` +
            `${language}: ${JSON.stringify(value)}). Language names are endonyms and must be identical.`,
        )
      }
    }
  }
}

// 5: every literal key a t() call names actually exists.
//
// Parity between en and zh says the two files agree; it says nothing about
// whether the console asks for keys either of them has. A typo ships silently
// and renders as the raw key - "identity.column.streak" in the middle of a
// drawer - which no test and no type checker sees. Caught exactly that way
// once, which is why this exists.
//
// Literal keys only. A key built from a variable - t(`x.${kind}`) - cannot be
// resolved here, and guessing at the possible values would produce false
// failures on the one pattern the console uses most.
const src = resolve(here, '..', 'src')
const CALL = /\bt\(\s*'([a-z][\w.]*:)?([\w.]+)'/g
const DEFAULTED = /defaultValue/

function* sources(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) {
      if (entry.name !== 'locales') yield* sources(full)
    } else if (/\.tsx?$/.test(entry.name)) {
      yield full
    }
  }
}

for (const file of sources(src)) {
  const text = readFileSync(file, 'utf8')
  for (const [whole, prefix, key] of text.matchAll(CALL)) {
    const namespace = prefix ? prefix.slice(0, -1) : null
    // No namespace means whichever the component declared; the console mixes
    // several, so check the key against all of them and only complain when no
    // namespace has it.
    const candidates = namespace ? [namespace] : NAMESPACES
    if (namespace && !NAMESPACES.includes(namespace)) continue
    const found = candidates.some((ns) => catalogs.get(`en:${ns}`)?.has(key))
    if (found) continue
    // A call site that supplies its own fallback is saying the key may be
    // absent, which is how the endpoint and tag labels are written.
    const at = text.indexOf(whole)
    if (DEFAULTED.test(text.slice(at, at + 400))) continue
    problems.push(
      `${relative(src, file)} asks for "${namespace ? `${namespace}:` : ''}${key}", ` +
        'which no catalogue has',
    )
  }
}

if (problems.length > 0) {
  console.error('i18n check failed:')
  for (const problem of problems) console.error(`  - ${problem}`)
  process.exit(1)
}

console.log(
  'i18n check passed: en/zh key sets match, ICU parses, all error codes covered, ' +
    'language names are endonyms, every t() key resolves',
)
