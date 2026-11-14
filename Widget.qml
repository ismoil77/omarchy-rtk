import QtQuick
import Qt.labs.folderlistmodel
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "kit" as Kit
import "Model.js" as Model
import "kit/I18n.js" as I18n

// rtk widget: tokens saved today in the bar, and a popup for the chosen
// period — a chart by hour or day, totals, how often the hook rewrote a
// command, projects (their worktrees grouped together) and the commands that
// saved the most. Texts come in English or Russian (`language`); the popup is
// held to WCAG AA contrast and scales with `textSize`.
//
//   left click   - open/close the popup
//   right click  - refresh now
//   middle click - `rtk gain --graph` in a floating terminal
//   popup        - period buttons (also 1/2/3 or p), ←/→ or hover to read a
//                  column, ↑/↓ or a click pick a project for its copies and
//                  top commands (click it again to go back), m shows more
//                  projects, r refreshes, Enter opens the report
Panel {
  id: root
  moduleName: "ismoil.rtk"
  ipcTarget: "ismoil.rtk"
  manageIpc: false

  readonly property string period: Model.validPeriod(setting("period", "today"))
  // rtk writes its history as it works: the widget re-reads when the folder
  // changes, at most every `interval` seconds, and every 10 minutes regardless.
  readonly property int pollSeconds: Math.max(5, Number(setting("interval", 30)) || 30)
  readonly property string databasePath: {
    // "~/x" as Python reads it: the watcher below needs the real path too.
    var set = String(setting("database", "")).trim()
    if (set === "~" || set.indexOf("~/") === 0) set = Quickshell.env("HOME") + set.slice(1)
    if (set !== "") return set
    var data = Quickshell.env("XDG_DATA_HOME") || (Quickshell.env("HOME") + "/.local/share")
    return data + "/rtk/history.db"
  }
  property bool dirty: true
  readonly property var cfg: Model.config({ language: setting("language", "auto"), locale: Qt.locale().name })

  readonly property bool vertical: bar ? bar.vertical : false
  readonly property string icon: "󰓅"
  readonly property string script: localPath(Qt.resolvedUrl("stats.py"))

  property var today: Model.emptyTotals()
  property bool todayOk: false
  property string todayDay: ""
  property var stats: Model.empty(period)
  property string selected: ""
  property bool allProjects: false
  property bool allCopies: false
  // Chart column under the mouse or picked with ←/→; -1 is the current one.
  property int cursor: -1
  // The mode changed while stats.py was still running: its answer is for the
  // old mode, so one more run follows it.
  property bool rerun: false
  // What the run in flight was asked for, and whether it answered.
  property string runMode: "bar"
  property bool answered: false

  readonly property var project: Model.findProject(stats, selected)
  readonly property var topCommands: project ? project.top : stats.top
  readonly property var tableWidths: [countMetrics.width, tokenMetrics.width, pctMetrics.width]

  function tr(key, params) { return Model.tr(cfg, key, params) }

  // The version for the signature, from the manifest so it never drifts.
  FileView { id: manifestFile; path: root.localPath(Qt.resolvedUrl("manifest.json")); blockLoading: true }
  readonly property string version: { try { return JSON.parse(manifestFile.text()).version || "" } catch (e) { return "" } }

  // file:///a%20b/x.py -> /a b/x.py
  function localPath(url) {
    return decodeURIComponent(String(url).replace(/^file:\/\//, ""))
  }

  Kit.ThemePalette { id: themePalette }
  Kit.Look {
    id: skin
    textSize: String(root.setting("textSize", "Normal"))
    animations: root.setting("animations", true) !== false
    fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
    themeColors: themePalette.colors
  }

  // Each write of rtk opens and drops `history.db-wal` beside the database:
  // watching the folder costs nothing and never reads the database itself.
  FolderListModel {
    folder: "file://" + root.databasePath.replace(/\/[^\/]*$/, "")
    nameFilters: [root.databasePath.replace(/.*\//, "") + "*"]
    showDirs: false
    // Reading a WAL database makes SQLite create and drop history.db-shm/-wal
    // itself: changes during the widget's own run, or just after, are its own.
    function changed() { if (!statsProc.running && Date.now() - root.lastExit > 1500) root.dirty = true }
    onRowsInserted: changed()
    onRowsRemoved: changed()
    onDataChanged: changed()
  }

  TextMetrics { id: countMetrics; font.family: skin.fontFamily; font.pixelSize: skin.small; text: Model.countSample(root.stats) }
  TextMetrics { id: tokenMetrics; font.family: skin.fontFamily; font.pixelSize: skin.small; text: "000.0K" }
  TextMetrics { id: pctMetrics; font.family: skin.fontFamily; font.pixelSize: skin.small; text: "100%" }

  // The mode is fixed when a run starts: a binding on the command could
  // still hold the old mode while opened/period notifications land.
  function refresh() {
    if (statsProc.running) {
      rerun = true
      return
    }
    runMode = opened ? period : "bar"
    answered = false
    // The database path goes by environment, not as an argument: other local
    // accounts can read a process's command line.
    statsProc.environment = String(setting("database", "")).trim() !== "" ? ({ ISMOIL_RTK_DB: databasePath }) : ({})
    statsProc.command = ["python3", "-I", script, runMode]
    statsProc.running = true
  }

  // `raw` is what stats.py printed; no usable answer is an error in its own right.
  function applyStats(raw) {
    answered = true
    var next = Model.parse(raw) || Model.failure(runMode, "internal")
    if (next.todayOk) {
      today = next.today
      todayOk = true
      todayDay = Model.dayKey(new Date())
    } else if (next.error !== "busy" || todayDay !== Model.dayKey(new Date())) {
      // Gone, unreadable or changed, or kept through a busy database past midnight:
      // do not keep showing a number that may be from yesterday.
      todayOk = false
    }
    // The bar-only answer, or a late one for the period the user just left.
    if (next.period !== period) return
    // Only a busy database is worth waiting out: keep the last good numbers.
    if (next.error === "busy" && stats.ok && stats.period === period) return
    stats = next
    if (selected !== "" && !Model.findProject(stats, selected)) selected = ""
  }

  function setPeriod(next) {
    if (Model.PERIODS.indexOf(next) < 0 || next === period) return
    cursor = -1
    settings = Object.assign({}, settings, { period: next })
    if (bar && bar.shell && typeof bar.shell.updateEntryInline === "function")
      bar.shell.updateEntryInline(moduleName, settings)
    Qt.callLater(refresh)
  }

  function moveCursor(step) {
    var count = stats.chart.length
    if (count === 0) return
    var from = cursor < 0 ? stats.chartNow : cursor
    cursor = Math.max(0, Math.min(count - 1, from + step))
  }

  function toggleProject(id) {
    selected = selected === id ? "" : id
    allCopies = false
  }

  // ↑/↓ walk the project rows: the copies and top commands of the picked one appear below.
  function moveProject(step) {
    selected = Model.stepProject(stats, selected, allProjects, step)
    allCopies = false
  }

  function openReport() {
    close()
    // Summary, commands and the 30-day graph, paged from the top: the whole
    // report is taller than the floating terminal.
    if (bar) bar.run("omarchy-launch-floating-terminal-with-presentation 'rtk gain --graph | less -R'")
  }

  IpcHandler {
    target: "ismoil.rtk"

    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): void { root.refresh() }
    function showPeriod(name: string): void { root.setPeriod(name) }
    function showProject(id: string): void { root.toggleProject(id) }
  }

  // Closed, the bar only needs today's total (`stats.py bar`); the full
  // report for the period runs while the popup is open.
  Process {
    id: statsProc
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.applyStats(text) }
    // python3 missing or killed: nothing was printed. The output can still be
    // on its way when the exit is reported, so the verdict waits a moment.
    onExited: function(code, status) { root.lastExit = Date.now(); silence.restart() }
    onRunningChanged: {
      if (running || !root.rerun) return
      root.rerun = false
      root.refresh()
    }
  }

  Timer {
    id: silence
    interval: 500
    onTriggered: if (!root.answered && !statsProc.running) root.applyStats("")
  }

  // A run when rtk wrote something since the last one (at most every
  // `interval` s, 5 s while open), and every 10 minutes anyway (midnight, a
  // missed event).
  property real lastRun: 0
  property real lastExit: 0
  Timer {
    // "All time" reads the whole history (about a second of CPU on a large one).
    interval: root.opened ? (root.period === "all" ? 15000 : 5000) : root.pollSeconds * 1000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: {
      if (!root.dirty && Date.now() - root.lastRun < 600000) return
      root.dirty = false
      root.lastRun = Date.now()
      root.refresh()
    }
  }

  onOpenedChanged: {
    cursor = -1
    if (opened) {
      Qt.callLater(refresh)
    } else {
      allProjects = false
    }
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  TextMetrics {
    id: barLabelMetrics
    font.family: button.fontFamily
    font.pixelSize: button.fontSize
    text: button.labelText
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    // Omarchy 4.5: `text` is a single icon glyph; the number goes in `labelText`.
    text: root.icon
    labelText: root.vertical ? "" : Model.barLabel(root.cfg, root.today, root.todayOk)
    slotSize: root.vertical ? Style.bar.iconSlot : Math.ceil(button.canvasSize + barLabelMetrics.width + Style.space(4)) + Style.space(14)
    tooltipText: root.opened ? "" : Model.tooltip(root.cfg, root.today, root.todayOk)
    onPressed: function(b) {
      if (b === Qt.RightButton) root.refresh()
      else if (b === Qt.MiddleButton) root.openReport()
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: panel.loadedContent
    contentWidth: panel.fittedContentWidth(Math.round(Style.space(380) * skin.textScale))
    contentHeight: panel.fittedContentHeight(panel.loadedContentHeight + skin.bottomPad)

    // Built the first time the popup opens, not at shell start on every monitor.
    content: Component {
      PanelKeyCatcher {
        id: keyCatcher
        anchors.fill: parent
        implicitHeight: column.implicitHeight
        onActivateRequested: root.openReport()
        onCloseRequested: root.close()
        onTabRequested: function(direction) { root.switchPanel(direction) }
        onMoveRequested: function(dx, dy) {
          if (dx !== 0) root.moveCursor(dx)
          if (dy !== 0) root.moveProject(dy)
        }
        onTextKey: function(t) {
          t = I18n.latinKey(t)
          if (t === "r") root.refresh()
          else if (t >= "1" && t <= "3") root.setPeriod(Model.PERIODS[Number(t) - 1])
          else if (t === "p") root.setPeriod(Model.nextPeriod(root.period))
          else if (t === "m") root.allProjects = !root.allProjects
        }

        // A long project list or large text can outgrow the screen: it scrolls.
        Flickable {
          id: scroller
          // Reopen at the top, not where it was scrolled to last time.
          Connections {
            target: root
            function onOpenedChanged() { if (!root.opened) scroller.contentY = 0 }
          }
          anchors.fill: parent
          contentWidth: width
          contentHeight: column.implicitHeight + skin.bottomPad
          interactive: contentHeight > height
          boundsBehavior: Flickable.StopAtBounds
          clip: true

          Column {
            id: column
            width: scroller.width
            spacing: Style.space(12)

            Kit.Header {
              width: parent.width
              look: skin
              glyph: root.icon
              name: root.tr("title") + " · " + Model.periodLabel(root.cfg, root.period)
              value: root.stats.ok ? Model.tokens(root.cfg, root.stats.total.saved) : "—"
              say: root.stats.ok ? root.tr("savedSay", { pct: Model.pct(root.stats.total.pct) }) : Model.subtitle(root.cfg, root.stats)
              chip: root.stats.ok ? Model.commands(root.cfg, root.stats.total.commands) : ""
              tone: skin.accentFill
              toneText: skin.accentText
            }

            // ---------- Period picker ----------
            Row {
              width: parent.width
              spacing: Style.space(4)

              Repeater {
                model: Model.PERIODS

                delegate: Kit.ActionButton {
                  required property var modelData
                  required property int index
                  look: skin
                  compact: true
                  width: (parent.width - parent.spacing * 2) / 3
                  text: Model.periodLabel(root.cfg, modelData)
                  selected: root.period === modelData
                  onClicked: root.setPeriod(modelData)
                }
              }
            }

            // ---------- Chart: saved tokens per hour (today) or per day ----------
            Kit.Card {
              width: parent.width
              look: skin

            Kit.BarChart {
              id: chart
              width: parent.width
              look: skin
              values: Model.chartValues(root.stats.chart)
              ticks: Model.chartTicks(root.cfg, root.stats.period, root.stats.chart)
              current: root.stats.chartNow
              cursor: root.cursor
              title: Model.slotTitle(root.cfg, root.stats.period, root.stats.chart[shown] || null, shown === current)
              titleSample: Model.slotTitleSample(root.cfg, root.stats.period, root.stats.chart)
              detail: Model.slotDetail(root.cfg, root.stats.chart[shown] || null)
              detailSample: Model.slotDetailSample(root.cfg, root.stats.chart)
              onColumnHovered: function(index) { root.cursor = index }
              onColumnLeft: root.cursor = -1
            }
            }

            // ---------- Where the tokens went ----------
            Kit.Section {
              width: parent.width
              look: skin
              spacing: Style.spacing.labelGap

              Kit.Meter {
                width: parent.width
                look: skin
                fromRight: true
                fraction: Model.fraction(root.stats.total.saved, root.stats.total.input)
              }
              Kit.InfoPair { look: skin; label: root.tr("rowReached"); value: Model.tokens(root.cfg, root.stats.total.output) }
              Kit.InfoPair { look: skin; label: root.tr("rowWithout"); value: Model.tokens(root.cfg, root.stats.total.input) }
              Kit.InfoPair { look: skin; label: root.tr("rowHook"); value: Model.rewriteShare(root.cfg, root.stats.hook) }
              Kit.InfoPair { look: skin; label: root.tr("rowAvg"); value: root.tr("rowAvgValue", { n: root.stats.total.avgMs }) }
              // rtk printed more than the raw command (git add, git fetch); like
              // `rtk gain`, those rows count as zero saved, not as a loss.
              Kit.InfoPair {
                look: skin
                visible: root.stats.worse > 0
                label: root.tr("rowWorse")
                value: Model.times(root.cfg, root.stats.worse)
                valueColor: skin.urgentText
              }
              // Over Claude Code's 30k-char Bash cap the agent never saw the raw
              // output, so `rtk gain` leaves these rows out; so does this widget.
              Kit.InfoPair {
                look: skin
                visible: root.stats.oversized > 0
                label: root.tr("rowOversized")
                value: Model.times(root.cfg, root.stats.oversized)
              }
            }

            // ---------- Projects ----------
            Kit.Section {
              id: projectList
              width: parent.width
              look: skin
              caption: root.tr("sectionProjects")
              hint: root.tr("columnsHint")
              spacing: Style.space(4)

              Text {
                visible: root.stats.projects.length === 0
                textFormat: Text.PlainText
                text: root.tr("noRuns")
                color: skin.dim
                font.family: skin.fontFamily
                font.pixelSize: skin.small
              }

              Repeater {
                model: Model.visibleRows(root.stats.projects, root.allProjects)

                delegate: Kit.StatRow {
                  required property var modelData
                  width: projectList.width - projectList.padding * 2
                  look: skin
                  name: Model.projectName(root.cfg, modelData) + (modelData.copies.length > 1 ? "  · " + Model.copies(root.cfg, modelData.copies.length) : "")
                  cells: ["×" + modelData.commands, Model.tokens(root.cfg, modelData.saved), Model.pct(modelData.pct)]
                  cellWidths: root.tableWidths
                  dimCells: [true, false, true]
                  share: Model.fraction(modelData.saved, root.stats.total.saved)
                  highlighted: root.selected === modelData.id
                  clickable: true
                  onClicked: root.toggleProject(modelData.id)
                }
              }

              Kit.MoreButton {
                look: skin
                text: Model.moreLabel(root.cfg, root.stats.projects, root.allProjects)
                onClicked: root.allProjects = !root.allProjects
              }
            }

            // ---------- Selected project: its copies ----------
            Kit.Section {
              id: copyList
              visible: root.project !== null && root.project.copies.length > 1
              width: parent.width
              look: skin
              caption: root.project ? root.tr("sectionCopies", { name: Model.projectName(root.cfg, root.project) }) : ""
              hint: root.tr("columnsHint")
              spacing: Style.space(4)

              Repeater {
                model: root.project ? Model.visibleRows(root.project.copies, root.allCopies) : []

                delegate: Kit.StatRow {
                  required property var modelData
                  width: copyList.width - copyList.padding * 2
                  look: skin
                  name: Model.copyName(root.cfg, modelData)
                  cells: ["×" + modelData.commands, Model.tokens(root.cfg, modelData.saved), Model.pct(modelData.pct)]
                  cellWidths: root.tableWidths
                  dimCells: [true, false, true]
                  share: root.project ? Model.fraction(modelData.saved, root.project.saved) : 0
                }
              }

              Kit.MoreButton {
                look: skin
                text: root.project ? Model.moreLabel(root.cfg, root.project.copies, root.allCopies) : ""
                onClicked: root.allCopies = !root.allCopies
              }
            }

            // ---------- Top commands (selected project or everything) ----------
            Kit.Section {
              id: commandList
              visible: root.topCommands.length > 0
              width: parent.width
              look: skin
              caption: root.project ? root.tr("sectionTopOf", { name: Model.projectName(root.cfg, root.project) }) : root.tr("sectionTop")
              hint: root.tr("columnsHint")
              spacing: Style.space(4)

              Repeater {
                model: root.topCommands

                delegate: Kit.StatRow {
                  required property var modelData
                  width: commandList.width - commandList.padding * 2
                  look: skin
                  name: modelData.cmd
                  cells: ["×" + modelData.commands, Model.tokens(root.cfg, modelData.saved), Model.pct(modelData.pct)]
                  cellWidths: root.tableWidths
                  dimCells: [true, false, true]
                  share: Model.fraction(modelData.saved, root.project ? root.project.saved : root.stats.total.saved)
                }
              }
            }

            // ---------- Actions ----------
            Kit.ActionRow {
              width: parent.width

              Kit.ActionButton {
                look: skin
                iconText: "󰆍"
                text: root.tr("report")
                primary: true
                onClicked: root.openReport()
              }
              Kit.ActionButton {
                look: skin
                iconText: "󰑐"
                text: root.tr("refresh")
                onClicked: root.refresh()
              }
            }

            Kit.Signature {
              width: parent.width
              look: skin
              widget: "rtk"
              extra: root.stats.version ? "rtk " + root.stats.version : ""
              version: root.version
            }
          }
        }
      }
    }
  }
}
