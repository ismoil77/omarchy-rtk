import QtQuick
import QtTest
import "../Model.js" as Model
import "../Strings.js" as Strings
import "../kit/I18n.js" as I18n
import "../kit/Contrast.js" as Contrast

// The same libraries the widget imports, run by Qt's own JavaScript engine:
// a syntax Node accepts but QML does not would fail here.
TestCase {
  name: "Model in the QML engine"

  function test_libraries_load_and_answer() {
    var en = Model.config({ language: "en" })
    var ru = Model.config({ language: "ru" })
    compare(en.lang, "en")
    compare(Model.tokens(en, 34100), "34.1K")
    compare(Model.tokens(ru, 34100), "34,1K")
    compare(Model.commands(ru, 3), "3 команды")
    compare(I18n.num("ru", 7.2, 1), "7,2")
    compare(Contrast.ratio("#000000", "#ffffff").toFixed(0), "21")
    verify(Strings.langs.length >= 2)
  }

  function test_texts() {
    var en = Model.config({ language: "en" })
    compare(Model.subtitle(en, { ok: true, period: "week", total: { saved: 5000 } }), "Saved 5.0K over 7 days")
    compare(Model.slotTitle(en, "today", { key: "15" }, true), "15:00–16:00")
    compare(Model.moreLabel(en, [1, 2, 3, 4, 5, 6, 7], false), "2 more")
  }

  function test_parse() {
    compare(Model.parse("nope"), null)
    compare(Model.parse('{"ok":true}').ok, true)
  }
}
