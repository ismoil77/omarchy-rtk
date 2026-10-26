import test from "node:test"
import assert from "node:assert/strict"
import { Strings } from "./loader.mjs"

const S = Strings()
const placeholders = text => (String(text).match(/\{\w+\}/g) || []).sort().join(",")

test("every language has exactly the keys English has", () => {
  const keys = Object.keys(S.tables.en).sort()
  for (const lang of S.langs) {
    assert.deepEqual(Object.keys(S.tables[lang]).sort(), keys, lang)
  }
})

test("translations keep the placeholders of the English text", () => {
  for (const lang of S.langs) {
    for (const [key, en] of Object.entries(S.tables.en)) {
      const text = S.tables[lang][key]
      if (Array.isArray(en)) continue
      assert.equal(placeholders(text), placeholders(en), `${lang}.${key}`)
    }
  }
})

test("noun forms: one entry per plural class of the language", () => {
  const classes = { en: 2, ru: 3 }
  for (const lang of S.langs) {
    for (const [key, value] of Object.entries(S.tables[lang])) {
      if (Array.isArray(value)) assert.equal(value.length, classes[lang], `${lang}.${key}`)
    }
  }
})

test("no empty texts", () => {
  for (const lang of S.langs) {
    for (const [key, value] of Object.entries(S.tables[lang])) {
      assert.ok(Array.isArray(value) ? value.every(Boolean) : String(value).length > 0, `${lang}.${key}`)
    }
  }
})

test("Russian is Russian and English is not", () => {
  const cyr = /[А-Яа-яЁё]/
  for (const [key, value] of Object.entries(S.tables.en)) {
    assert.ok(![].concat(value).some(v => cyr.test(v)), `en.${key}`)
  }
  assert.ok(cyr.test(S.tables.ru.report))
})
