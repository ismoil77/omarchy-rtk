.pragma library

// Every user-visible word of the widget, per language. {name} placeholders
// are filled by I18n.t; an array is the forms of a noun (see I18n.plural).
// Tests keep the two tables in step.

var en = {
  title: "rtk",
  loading: "Reading the database…",
  errNoDb: "rtk has no history yet",
  errBusy: "The rtk database is busy",
  errSchema: "The rtk database has a layout this widget does not know",
  errUnreadable: "The rtk database cannot be read",
  errInternal: "The reader failed to run (is python3 installed?)",

  periodToday: "Today",
  periodWeek: "7 days",
  periodAll: "All time",
  sinceToday: "today",
  sinceWeek: "over 7 days",
  sinceAll: "all time",
  periodKey: "Key {n}",

  savedLine: "Saved {n} {since}",
  tipSaved: "rtk today: saved {saved} of {input} ({pct}) · {commands}",
  tipNone: "rtk",

  reached: "Reached model {n}",
  tileSaved: "Saved",
  tileCommands: "Commands",
  tileAvg: "average {n} ms",
  without: "{n} without rtk",
  rowCommands: "Commands through rtk",
  rowHook: "Hook rewrote",
  rowHookValue: "{n} of {total} ({pct})",
  rowAvg: "Average command time",
  rowAvgValue: "{n} ms",
  rowWorse: "Output got longer",
  rowOversized: "Not counted: output > 30k chars",

  sectionProjects: "Projects",
  sectionCopies: "{name} — copies",
  sectionTop: "Top commands",
  sectionTopOf: "Top commands — {name}",
  projTmp: "temporary folders",
  projHome: "~ home folder",
  copyMain: "main copy",
  copies: ["copy", "copies"],
  more: "{n} more",
  less: "Collapse",
  noRuns: "rtk did not run in this period",
  noRunsSlot: "rtk did not run",

  report: "Full report",
  reportTip: "rtk gain --graph in a terminal, q to close",
  refresh: "Refresh",
  refreshTip: "Refresh · R",

  commandsN: ["command", "commands"],
  timesN: ["time", "times"],
  slotDetail: "{saved} of {input} · {pct} · {commands}",
  today: "Today",
  dayTitle: "{weekday}, {day} {month}",

  wd0: "Sun", wd1: "Mon", wd2: "Tue", wd3: "Wed", wd4: "Thu", wd5: "Fri", wd6: "Sat",
  mo0: "Jan", mo1: "Feb", mo2: "Mar", mo3: "Apr", mo4: "May", mo5: "Jun",
  mo6: "Jul", mo7: "Aug", mo8: "Sep", mo9: "Oct", mo10: "Nov", mo11: "Dec"
}

var ru = {
  title: "rtk",
  loading: "Читаю базу…",
  errNoDb: "У rtk ещё нет истории",
  errBusy: "База rtk занята",
  errSchema: "У базы rtk незнакомое виджету устройство",
  errUnreadable: "Базу rtk не удаётся прочитать",
  errInternal: "Не удалось запустить чтение (есть ли python3?)",

  periodToday: "Сегодня",
  periodWeek: "7 дней",
  periodAll: "Всё время",
  sinceToday: "за сегодня",
  sinceWeek: "за 7 дней",
  sinceAll: "за всё время",
  periodKey: "Клавиша {n}",

  savedLine: "Сэкономлено {n} {since}",
  tipSaved: "rtk сегодня: сэкономлено {saved} из {input} ({pct}) · {commands}",
  tipNone: "rtk",

  reached: "Дошло до модели {n}",
  tileSaved: "Сэкономлено",
  tileCommands: "Команды",
  tileAvg: "в среднем {n} мс",
  without: "{n} без rtk",
  rowCommands: "Команд через rtk",
  rowHook: "Хук переписал",
  rowHookValue: "{n} из {total} ({pct})",
  rowAvg: "Среднее время команды",
  rowAvgValue: "{n} мс",
  rowWorse: "Вывод стал длиннее",
  rowOversized: "Не в счёт: вывод > 30k символов",

  sectionProjects: "Проекты",
  sectionCopies: "{name} — копии",
  sectionTop: "Топ команд",
  sectionTopOf: "Топ команд — {name}",
  projTmp: "временные папки",
  projHome: "~ домашняя папка",
  copyMain: "основная копия",
  copies: ["копия", "копии", "копий"],
  more: "Ещё {n}",
  less: "Свернуть",
  noRuns: "За этот период rtk не запускался",
  noRunsSlot: "rtk не запускался",

  report: "Полный отчёт",
  reportTip: "rtk gain --graph в терминале, q — закрыть",
  refresh: "Обновить",
  refreshTip: "Обновить · R",

  commandsN: ["команда", "команды", "команд"],
  timesN: ["раз", "раза", "раз"],
  slotDetail: "{saved} из {input} · {pct} · {commands}",
  today: "Сегодня",
  dayTitle: "{weekday}, {day} {month}",

  wd0: "вс", wd1: "пн", wd2: "вт", wd3: "ср", wd4: "чт", wd5: "пт", wd6: "сб",
  mo0: "янв", mo1: "фев", mo2: "мар", mo3: "апр", mo4: "мая", mo5: "июн",
  mo6: "июл", mo7: "авг", mo8: "сен", mo9: "окт", mo10: "ноя", mo11: "дек"
}

var tables = { en: en, ru: ru }
var langs = ["en", "ru"]
