.pragma library
.import "kit/I18n.js" as I18n
.import "Strings.js" as Strings

// Pure logic of the rtk widget: parsing stats.py, formatting, the chart's
// labels. No QML here, so tests run it in plain Node.

var PERIODS = ["today", "week", "all"]
var PERIOD_KEYS = { today: "periodToday", week: "periodWeek", all: "periodAll" }
var SINCE_KEYS = { today: "sinceToday", week: "sinceWeek", all: "sinceAll" }
// A long list shows its first SHORT_LIST rows until it is expanded.
var SHORT_LIST = 5

// `raw` = { language, locale }; `lang` is what every text function reads.
function config(raw) {
  return { lang: I18n.resolve(raw && raw.language, raw && raw.locale, Strings.langs, { "русский": "ru", english: "en" }) }
}

function tr(cfg, key, params) {
  return I18n.t(Strings.tables, cfg.lang, key, params)
}

function emptyTotals() {
  return { commands: 0, input: 0, output: 0, saved: 0 }
}

function empty(period) {
  return {
    ok: false,
    error: "",
    detail: "",
    todayOk: false,
    period: period || "today",
    version: "",
    today: emptyTotals(),
    total: { commands: 0, input: 0, output: 0, saved: 0, pct: 0, avgMs: 0 },
    chart: [],
    chartNow: -1,
    hook: { seen: 0, rewritten: 0, passed: 0 },
    worse: 0,
    oversized: 0,
    projects: [],
    top: []
  }
}

// What the widget shows when stats.py gave no answer at all (it crashed, or
// python is missing): an error state for the mode that was asked.
function failure(period, code) {
  return Object.assign(empty(period), { error: code || "internal" })
}

// stats.py prints one JSON object; anything else is ignored so the widget
// keeps the last good numbers.
function parse(raw) {
  var data
  try {
    data = JSON.parse(String(raw || "").trim())
  } catch (e) {
    return null
  }
  if (!data || typeof data !== "object") return null
  return Object.assign(empty(data.period), data)
}

function validPeriod(value) {
  return PERIODS.indexOf(String(value)) >= 0 ? String(value) : "today"
}

function nextPeriod(period) {
  return PERIODS[(PERIODS.indexOf(period) + 1) % PERIODS.length]
}

// ---------- numbers ----------

// Token counts the way rtk prints them: 850, 34.1K, 1.2M (a comma in Russian).
function tokens(cfg, n) {
  var a = Math.max(0, Number(n) || 0)
  if (a >= 1000000) return I18n.num(cfg.lang, a / 1000000, 1) + "M"
  if (a >= 1000) return I18n.num(cfg.lang, a / 1000, 1) + "K"
  return String(Math.round(a))
}

function pct(value) {
  return Math.round(Number(value) || 0) + "%"
}

function share(part, whole) {
  var w = Number(whole) || 0
  return w > 0 ? (Number(part) || 0) / w * 100 : 0
}

// A part of a whole, clamped to 0..1, for bars.
function fraction(part, whole) {
  var w = Number(whole) || 0
  if (w <= 0) return 0
  return Math.max(0, Math.min(1, (Number(part) || 0) / w))
}

// The widest "×N" a list can hold: the total is at least any row's count.
function countSample(stats) {
  return "×" + "0".repeat(Math.max(3, String(Math.round(Number(stats.total.commands) || 0)).length))
}

function commands(cfg, n) { return I18n.count(Strings.tables, cfg.lang, "commandsN", n) }
function times(cfg, n) { return I18n.count(Strings.tables, cfg.lang, "timesN", n) }
function copies(cfg, n) { return I18n.count(Strings.tables, cfg.lang, "copies", n) }

// ---------- texts ----------

function periodLabel(cfg, period) { return tr(cfg, PERIOD_KEYS[period] || PERIOD_KEYS.today) }

var ERROR_KEYS = { nodb: "errNoDb", busy: "errBusy", schema: "errSchema", unreadable: "errUnreadable", internal: "errInternal" }

// The line under the title: "Saved 119.7K today", or why there is nothing yet.
function subtitle(cfg, stats) {
  if (stats.ok) return tr(cfg, "savedLine", { n: tokens(cfg, stats.total.saved), since: tr(cfg, SINCE_KEYS[stats.period] || SINCE_KEYS.today) })
  if (!stats.error) return tr(cfg, "loading")
  // A code this widget does not know (a newer stats.py) is still an error, not "Loading…".
  return tr(cfg, ERROR_KEYS[stats.error] || "errInternal")
}

// Local calendar day of `date`, "YYYY-MM-DD": today's numbers kept across a busy
// database are only good for the day they were read.
function dayKey(date) {
  return date.getFullYear() + "-" + ("0" + (date.getMonth() + 1)).slice(-2) + "-" + ("0" + date.getDate()).slice(-2)
}

function barLabel(cfg, today, ok) {
  return ok ? tokens(cfg, today.saved) : "…"
}

function tooltip(cfg, today, ok) {
  if (!ok) return tr(cfg, "tipNone")
  return tr(cfg, "tipSaved", {
    saved: tokens(cfg, today.saved),
    input: tokens(cfg, today.input),
    pct: pct(share(today.saved, today.input)),
    commands: commands(cfg, today.commands)
  })
}

function rewriteShare(cfg, hook) {
  if (!hook || !hook.seen) return "—"
  return tr(cfg, "rowHookValue", { n: hook.rewritten, total: hook.seen, pct: pct(share(hook.rewritten, hook.seen)) })
}

// A project's name as shown: folders without a name of their own are words.
function projectName(cfg, project) {
  if (project.kind === "tmp") return tr(cfg, "projTmp")
  if (project.kind === "home") return tr(cfg, "projHome")
  return project.label || project.name
}

function copyName(cfg, copy) {
  return copy.main ? tr(cfg, "copyMain") : copy.name
}

function findProject(data, id) {
  if (!data || !id) return null
  var list = Array.isArray(data.projects) ? data.projects : []
  for (var i = 0; i < list.length; i++) {
    if (list[i].id === id) return list[i]
  }
  return null
}

// The project picked by stepping `step` rows through the visible list: down
// from the last row or up from the first one goes back to "none" (""). Steps
// from "none" enter at the top (down) or the bottom (up).
function stepProject(stats, selected, expanded, step) {
  var rows = visibleRows(stats && stats.projects, expanded)
  if (rows.length === 0) return ""
  var at = -1
  for (var i = 0; i < rows.length; i++) if (rows[i].id === selected) at = i
  var next = at < 0 ? (step > 0 ? 0 : rows.length - 1) : at + step
  return next < 0 || next >= rows.length ? "" : rows[next].id
}

function visibleRows(list, expanded) {
  var rows = Array.isArray(list) ? list : []
  return expanded ? rows : rows.slice(0, SHORT_LIST)
}

// Toggle under a cut list: "5 more" / "Collapse"; empty when nothing is hidden.
function moreLabel(cfg, list, expanded) {
  var rest = (Array.isArray(list) ? list.length : 0) - SHORT_LIST
  if (rest <= 0) return ""
  return expanded ? tr(cfg, "less") : tr(cfg, "more", { n: rest })
}

// ---------- chart ----------

// The widest readout any column of the chart can show, for sizing the line.
function slotDetailSample(cfg, slots) {
  var widest = { saved: 0, input: 0, commands: 0 }
  ;(slots || []).forEach(function(s) {
    widest.saved = Math.max(widest.saved, Number(s.saved) || 0)
    widest.input = Math.max(widest.input, Number(s.input) || 0)
    widest.commands = Math.max(widest.commands, Number(s.commands) || 0)
  })
  return slotDetail(cfg, widest)
}

// Day slots carry "YYYY-MM-DD"; read them as local dates, not UTC.
function slotDate(key) {
  var p = String(key || "").split("-")
  return new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]))
}

function chartValues(slots) {
  return (slots || []).map(function(s) { return Number(s.saved) || 0 })
}

// Label under each column, or "" to leave it blank: every 6th hour, every
// weekday, and about five dates across a month.
function chartTicks(cfg, period, slots) {
  var list = slots || []
  return list.map(function(slot, index) {
    if (period === "today") return index % 6 === 0 ? slot.key : ""
    var d = slotDate(slot.key)
    if (period === "week") return tr(cfg, "wd" + d.getDay())
    var step = Math.max(1, Math.ceil(list.length / 5))
    return (list.length - 1 - index) % step === 0 ? String(d.getDate()) : ""
  })
}

// Heading of the readout for one column. The current column is already lit,
// so only a day needs to say it is today.
function slotTitle(cfg, period, slot, isNow) {
  if (!slot) return ""
  if (period === "today") {
    var h = Number(slot.key)
    return slot.key + ":00–" + (h === 23 ? "24" : ("0" + (h + 1)).slice(-2)) + ":00"
  }
  if (isNow) return tr(cfg, "today")
  var d = slotDate(slot.key)
  return tr(cfg, "dayTitle", { weekday: tr(cfg, "wd" + d.getDay()), day: d.getDate(), month: tr(cfg, "mo" + d.getMonth()) })
}

// The longest title any slot can show, so the readout lays out the same for all.
function slotTitleSample(cfg, period, slots) {
  var widest = ""
  ;(slots || []).forEach(function(s, i) {
    var t = slotTitle(cfg, period, s, false)
    if (t.length > widest.length) widest = t
  })
  return widest
}

function slotDetail(cfg, slot) {
  if (!slot || !slot.commands) return tr(cfg, "noRunsSlot")
  return tr(cfg, "slotDetail", {
    saved: tokens(cfg, slot.saved),
    input: tokens(cfg, slot.input),
    pct: pct(share(slot.saved, slot.input)),
    commands: commands(cfg, slot.commands)
  })
}
