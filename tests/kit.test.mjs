import test from "node:test"
import assert from "node:assert/strict"
import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const read = f => fs.readFileSync(path.join(root, f), "utf8")

test("kit/ is the vendored widget kit: stamped with a version and complete", () => {
  assert.match(read("kit/VERSION").trim(), /^\d+\.\d+\.\d+$/)
  for (const f of ["Contrast.js", "I18n.js", "Look.qml", "BarChart.qml", "InfoPair.qml", "MoreButton.qml", "Meter.qml", "StatRow.qml"])
    assert.ok(fs.existsSync(path.join(root, "kit", f)), f)
})

test("every kit file the widget imports or uses exists", () => {
  const widget = read("Widget.qml") + read("Model.js")
  for (const m of widget.matchAll(/import\s+"([^"]+\.js)"/g)) assert.ok(fs.existsSync(path.join(root, m[1])), m[1])
  for (const m of widget.matchAll(/\bKit\.(\w+)\b/g)) assert.ok(fs.existsSync(path.join(root, "kit", m[1] + ".qml")), m[1])
})

test("the manifest's entry point and settings match the widget", () => {
  const manifest = JSON.parse(read("manifest.json"))
  assert.ok(fs.existsSync(path.join(root, manifest.entryPoints.barWidget)))
  const widget = read("Widget.qml")
  for (const field of manifest.barWidget.schema)
    assert.ok(widget.includes(`"${field.key}"`), `Widget.qml reads setting ${field.key}`)
  for (const key of Object.keys(manifest.barWidget.defaults))
    assert.ok(manifest.barWidget.schema.some(f => f.key === key), `default ${key} has a schema entry`)
})
