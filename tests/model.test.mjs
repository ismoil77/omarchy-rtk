import test from "node:test"
import assert from "node:assert/strict"
import { Model } from "./loader.mjs"

// Values built inside the loader's vm context are not reference-equal to ours: compare as JSON.
const same = (a, b) => assert.equal(JSON.stringify(a), JSON.stringify(b))

const M = Model()
const en = M.config({ language: "en" })
const ru = M.config({ language: "ru" })

const slot = (key, saved, input, commands) => ({ key, label: key, saved, input, commands })

test("config: explicit language, locale and fallback", () => {
  assert.equal(M.config({ language: "ru" }).lang, "ru")
  assert.equal(M.config({ language: "auto", locale: "ru_RU" }).lang, "ru")
  assert.equal(M.config({ language: "auto", locale: "en_US" }).lang, "en")
  assert.equal(M.config({ language: "auto", locale: "de_DE" }).lang, "en")
  assert.equal(M.config({ language: "Русский" }).lang, "ru")
  assert.equal(M.config({}).lang, "en")
  assert.equal(M.config().lang, "en")
})

test("parse: stats.py output, noise, and defaults", () => {
  const s = M.parse('{"ok":true,"period":"week","total":{"saved":5}}\n')
  assert.equal(s.ok, true)
  assert.equal(s.period, "week")
  assert.equal(s.total.saved, 5)
  same(s.projects, [])
  assert.equal(M.parse(""), null)
  assert.equal(M.parse("Traceback…"), null)
  assert.equal(M.parse("[1]").ok, false)
  assert.equal(M.parse("null"), null)
})

test("no answer at all is an error state for the asked mode", () => {
  const f = M.failure("bar")
  assert.equal(f.ok, false)
  assert.equal(f.todayOk, false)
  assert.equal(f.error, "internal")
  assert.equal(f.period, "bar")
  assert.equal(M.failure("week", "nodb").error, "nodb")
  assert.equal(M.empty().todayOk, false)
})

test("stepProject walks the visible rows and falls off either end back to none", () => {
  const stats = { projects: ["a", "b", "c", "d", "e", "f", "g"].map(id => ({ id })) }
  assert.equal(M.stepProject(stats, "", false, 1), "a")
  assert.equal(M.stepProject(stats, "", false, -1), "e")        // the last visible row
  assert.equal(M.stepProject(stats, "", true, -1), "g")         // expanded: the real last row
  assert.equal(M.stepProject(stats, "a", false, 1), "b")
  assert.equal(M.stepProject(stats, "b", false, -1), "a")
  assert.equal(M.stepProject(stats, "a", false, -1), "")
  assert.equal(M.stepProject(stats, "e", false, 1), "")
  assert.equal(M.stepProject(stats, "f", false, 1), "a")        // a selection that is not visible acts like none
  assert.equal(M.stepProject({ projects: [] }, "", false, 1), "")
  assert.equal(M.stepProject(undefined, "", false, 1), "")
})

test("periods", () => {
  assert.equal(M.validPeriod("week"), "week")
  assert.equal(M.validPeriod("year"), "today")
  assert.equal(M.validPeriod(undefined), "today")
  assert.equal(M.nextPeriod("today"), "week")
  assert.equal(M.nextPeriod("all"), "today")
})

test("tokens match rtk's style, with a decimal comma in Russian", () => {
  assert.equal(M.tokens(en, 850), "850")
  assert.equal(M.tokens(en, 999), "999")
  assert.equal(M.tokens(en, 34100), "34.1K")
  assert.equal(M.tokens(en, 1200000), "1.2M")
  assert.equal(M.tokens(ru, 119700), "119,7K")
  assert.equal(M.tokens(en, -5), "0")
  assert.equal(M.tokens(en, "x"), "0")
})

test("pct, share and fraction survive bad input", () => {
  assert.equal(M.pct(28.6), "29%")
  assert.equal(M.pct(undefined), "0%")
  assert.equal(M.share(1, 4), 25)
  assert.equal(M.share(1, 0), 0)
  assert.equal(M.fraction(5, 4), 1)
  assert.equal(M.fraction(-1, 4), 0)
  assert.equal(M.fraction(1, 0), 0)
})

test("counts use the right plural form", () => {
  assert.equal(M.commands(en, 1), "1 command")
  assert.equal(M.commands(en, 151), "151 commands")
  assert.equal(M.commands(ru, 1), "1 команда")
  assert.equal(M.commands(ru, 3), "3 команды")
  assert.equal(M.commands(ru, 11), "11 команд")
  assert.equal(M.commands(ru, 151), "151 команда")
  assert.equal(M.times(ru, 38), "38 раз")
  assert.equal(M.times(en, 1), "1 time")
  assert.equal(M.copies(ru, 5), "5 копий")
})

test("countSample is as wide as the biggest count", () => {
  assert.equal(M.countSample({ total: { commands: 12 } }), "×000")
  assert.equal(M.countSample({ total: { commands: 19034 } }), "×00000")
  assert.equal(M.countSample({ total: { commands: 1234567 } }), "×0000000")
})

test("subtitle: saved line, and why there is none", () => {
  const ok = { ok: true, period: "today", total: { saved: 119700 } }
  assert.equal(M.subtitle(en, ok), "Saved 119.7K today")
  assert.equal(M.subtitle(ru, ok), "Сэкономлено 119,7K за сегодня")
  assert.equal(M.subtitle(en, { ...ok, period: "all" }), "Saved 119.7K all time")
  assert.equal(M.subtitle(en, { ok: false, error: "nodb" }), "rtk has no history yet")
  assert.equal(M.subtitle(en, { ok: false, error: "busy" }), "The rtk database is busy")
  assert.match(M.subtitle(en, { ok: false, error: "schema" }), /layout/)
  assert.match(M.subtitle(en, { ok: false, error: "unreadable" }), /cannot be read/)
  assert.match(M.subtitle(en, { ok: false, error: "internal" }), /python3/)
  assert.match(M.subtitle(ru, { ok: false, error: "schema" }), /устройство/)
  // A code from a newer stats.py is an error, never an endless "Loading…".
  assert.match(M.subtitle(en, { ok: false, error: "from-the-future" }), /python3/)
  assert.equal(M.subtitle(en, { ok: false, error: "" }), "Reading the database…")
  assert.equal(M.subtitle(ru, { ok: false, error: "" }), "Читаю базу…")
})

test("bar label and tooltip", () => {
  const today = { saved: 7500, input: 28100, commands: 151 }
  assert.equal(M.barLabel(en, today, true), "7.5K")
  assert.equal(M.barLabel(en, today, false), "…")
  assert.equal(M.tooltip(en, today, true), "rtk today: saved 7.5K of 28.1K (27%) · 151 commands")
  assert.equal(M.tooltip(ru, today, true), "rtk сегодня: сэкономлено 7,5K из 28,1K (27%) · 151 команда")
  assert.equal(M.tooltip(en, today, false), "rtk")
})

test("rewriteShare", () => {
  assert.equal(M.rewriteShare(en, { seen: 0 }), "—")
  assert.equal(M.rewriteShare(en, undefined), "—")
  assert.equal(M.rewriteShare(en, { seen: 1639, rewritten: 686 }), "686 of 1639 (42%)")
  assert.equal(M.rewriteShare(ru, { seen: 1639, rewritten: 686 }), "686 из 1639 (42%)")
})

test("project and copy names are words only where the data has none", () => {
  assert.equal(M.projectName(en, { kind: "tmp", name: "" }), "temporary folders")
  assert.equal(M.projectName(ru, { kind: "tmp", name: "" }), "временные папки")
  assert.equal(M.projectName(en, { kind: "home", name: "" }), "~ home folder")
  assert.equal(M.projectName(en, { kind: "repo", name: "app", label: "app (work)" }), "app (work)")
  assert.equal(M.projectName(en, { kind: "folder", name: "notes" }), "notes")
  assert.equal(M.copyName(en, { main: true, name: "" }), "main copy")
  assert.equal(M.copyName(ru, { main: true, name: "" }), "основная копия")
  assert.equal(M.copyName(en, { main: false, name: "lane2" }), "lane2")
})

test("findProject and the short list", () => {
  const data = { projects: [{ id: "/a" }, { id: "/b" }] }
  assert.equal(M.findProject(data, "/b").id, "/b")
  assert.equal(M.findProject(data, "/zzz"), null)
  assert.equal(M.findProject(data, ""), null)
  assert.equal(M.findProject(null, "/a"), null)
  const rows = Array.from({ length: 8 }, (_, i) => i)
  assert.equal(M.visibleRows(rows, false).length, 5)
  assert.equal(M.visibleRows(rows, true).length, 8)
  same(M.visibleRows(undefined, false), [])
  assert.equal(M.moreLabel(en, rows, false), "3 more")
  assert.equal(M.moreLabel(ru, rows, false), "Ещё 3")
  assert.equal(M.moreLabel(en, rows, true), "Collapse")
  assert.equal(M.moreLabel(en, rows.slice(0, 5), false), "")
  assert.equal(M.moreLabel(en, undefined, false), "")
})

test("chart ticks: every 6th hour, weekdays, ~5 dates in a month", () => {
  const hours = Array.from({ length: 24 }, (_, h) => slot(String(h).padStart(2, "0"), 0, 0, 0))
  const t = M.chartTicks(en, "today", hours)
  same(t.filter(Boolean), ["00", "06", "12", "18"])
  assert.equal(t.length, 24)

  // 2026-10-19 is a Monday.
  const week = Array.from({ length: 7 }, (_, d) => slot("2026-10-" + (19 + d), 0, 0, 0))
  same(M.chartTicks(en, "week", week), ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
  assert.equal(M.chartTicks(ru, "week", week)[0], "пн")

  const month = Array.from({ length: 30 }, (_, d) => slot("2026-10-" + String(d + 1).padStart(2, "0"), 0, 0, 0))
  const labels = M.chartTicks(en, "all", month)
  assert.equal(labels[29], "30")
  assert.ok(labels.filter(Boolean).length <= 6)
  same(M.chartTicks(en, "all", undefined), [])
})

test("chart values", () => {
  same(M.chartValues([slot("a", 5, 9, 1), slot("b", undefined, 0, 0)]), [5, 0])
  same(M.chartValues(undefined), [])
})

test("the widest slot title sets the readout layout", () => {
  const days = [slot("2026-10-20", 0, 0, 0), slot("2026-10-21", 0, 0, 0), slot("2026-10-22", 0, 0, 0)]
  assert.equal(M.slotTitleSample(en, "week", days), "Tue, 20 Oct")
  assert.equal(M.slotTitleSample(ru, "week", days), "вт, 20 окт")
  assert.equal(M.slotTitleSample(en, "today", [slot("09", 0, 0, 0), slot("23", 0, 0, 0)]), "09:00–10:00")
  assert.equal(M.slotTitleSample(en, "week", []), "")
  assert.equal(M.slotTitleSample(en, "week", undefined), "")
})

test("dayKey is the local calendar day", () => {
  assert.equal(M.dayKey(new Date(2026, 9, 5, 23, 59)), "2026-10-05")
  assert.equal(M.dayKey(new Date(2026, 11, 31, 0, 0)), "2026-12-31")
})

test("slot title and detail", () => {
  assert.equal(M.slotTitle(en, "today", slot("15", 0, 0, 0), true), "15:00–16:00")
  assert.equal(M.slotTitle(en, "today", slot("23", 0, 0, 0), false), "23:00–24:00")
  assert.equal(M.slotTitle(en, "today", slot("09", 0, 0, 0), false), "09:00–10:00")
  assert.equal(M.slotTitle(en, "week", slot("2026-10-26", 0, 0, 0), true), "Today")
  assert.equal(M.slotTitle(ru, "week", slot("2026-10-26", 0, 0, 0), true), "Сегодня")
  assert.equal(M.slotTitle(en, "week", slot("2026-10-25", 0, 0, 0), false), "Sun, 25 Oct")
  assert.equal(M.slotTitle(ru, "all", slot("2026-05-03", 0, 0, 0), false), "вс, 3 мая")
  assert.equal(M.slotTitle(en, "today", null, false), "")

  assert.equal(M.slotDetail(en, slot("15", 7500, 28100, 151)), "7.5K of 28.1K · 27% · 151 commands")
  assert.equal(M.slotDetail(ru, slot("15", 7500, 28100, 151)), "7,5K из 28,1K · 27% · 151 команда")
  assert.equal(M.slotDetail(en, slot("03", 0, 0, 0)), "rtk did not run")
  assert.equal(M.slotDetail(ru, null), "rtk не запускался")
})

test("the readout sample is the widest of the chart's columns", () => {
  const slots = [slot("a", 100, 400, 3), slot("b", 7500, 28100, 151), slot("c", 0, 0, 0)]
  assert.equal(M.slotDetailSample(en, slots), "7.5K of 28.1K · 27% · 151 commands")
  assert.equal(M.slotDetailSample(en, []), "rtk did not run")
  assert.equal(M.slotDetailSample(en, undefined), "rtk did not run")
})

test("day slots are read as local dates, not UTC", () => {
  const d = M.slotDate("2026-01-01")
  assert.equal(d.getDate(), 1)
  assert.equal(d.getMonth(), 0)
})
