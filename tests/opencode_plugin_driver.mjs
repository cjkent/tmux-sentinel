// Drives the OpenCode plugin with a stand-in for OpenCode's context, and writes what it
// would have run to a JSON file. Used by test_opencode_plugin.py; not a test on its own.
//
// usage: node opencode_plugin_driver.mjs <plugin.mjs> <scenario.json> <out.json>
import { readFileSync, writeFileSync } from "node:fs"
import { pathToFileURL } from "node:url"

const [pluginPath, scenarioPath, outPath] = process.argv.slice(2)
const scenario = JSON.parse(readFileSync(scenarioPath, "utf8"))

const calls = []
let bells = 0
const realWrite = process.stdout.write.bind(process.stdout)
process.stdout.write = (chunk) => {
  if (String(chunk).includes("\x07")) bells += 1
  return true
}

// Stand-in for Bun's shell: records the command, the stdin payload and the env.
function $(strings, ...values) {
  let cmd = ""
  let payload = null
  strings.forEach((s, i) => {
    cmd += s
    if (i < values.length) {
      if (Buffer.isBuffer(values[i])) {
        payload = JSON.parse(values[i].toString())
        cmd += "<STDIN>"
      } else {
        cmd += String(values[i])
      }
    }
  })
  const rec = { cmd: cmd.trim(), payload, env: null }
  calls.push(rec)
  const chain = {
    env(e) { rec.env = e; return chain },
    quiet() { return chain },
    nothrow() { return chain },
    // The file hook prints a bell for events that want attention.
    async text() { return rec.cmd.includes("hook.py") ? "\x07" : "" },
    then(resolve) { resolve(undefined) },
  }
  return chain
}

if (scenario.noTmux) delete process.env.TMUX_PANE
else process.env.TMUX_PANE = "%7"

const mod = await import(pathToFileURL(pluginPath).href)
const factories = Object.values(mod).filter((v) => typeof v === "function")
const hooks = await factories[0]({ $, directory: "/proj" })

for (const step of scenario.steps) {
  if (step.hook === "event") await hooks.event({ event: step.event })
  else await hooks[step.hook](step.input, step.output ?? {})
}

writeFileSync(outPath, JSON.stringify({
  exports: factories.length,
  hookNames: Object.keys(hooks),
  calls,
  bells,
}))
realWrite("")
