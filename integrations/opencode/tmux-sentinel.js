// MANAGED-BY-TMUX-SENTINEL — installed by setup.sh; edits are lost when it is re-run.
//
// Reports an OpenCode session's activity to tmux-sentinel, so the picker and the status
// bar can show whether it is working, waiting for approval, or done. It translates
// OpenCode's events into the same events the Claude Code and Kiro hooks send, and pipes
// them to tmux-sentinel's hook scripts.
//
// WRITTEN FROM THE DOCUMENTATION (https://opencode.ai/docs/plugins/), NOT FROM A RUNNING
// OPENCODE. The event names are documented; their payload fields are not, so every
// field read below is defensive and falls back to treating the event as the main
// session's. To see what OpenCode really sends, run it with:
//
//     TMUX_SENTINEL_OPENCODE_DEBUG=1 opencode
//
// and read ~/.tmux-sentinel/opencode-events.log. Only event types and the names of their
// fields are logged, never message content.

import { appendFileSync, mkdirSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"

const REPO = "@REPO_DIR@"
const DEBUG = Boolean(process.env.TMUX_SENTINEL_OPENCODE_DEBUG)
const LOG_DIR = join(homedir(), ".tmux-sentinel")

function debug(entry) {
  if (!DEBUG) return
  try {
    mkdirSync(LOG_DIR, { recursive: true })
    appendFileSync(
      join(LOG_DIR, "opencode-events.log"),
      JSON.stringify({ t: new Date().toISOString(), ...entry }) + "\n",
    )
  } catch {
    // Logging must never break the session.
  }
}

export const TmuxSentinel = async ({ $, directory }) => {
  // Outside tmux there is no pane to report to.
  if (!process.env.TMUX_PANE) return {}

  // sessionID -> parentID (null for a top-level session). A subagent runs as a child
  // session in the same pane and fires the same events, so its activity must not be
  // taken for the main session's; see the matching rule in tmux-sentinel's hook.
  const parents = new Map()
  // sessionID -> last status type, so "busy" is reported once per turn, not per update.
  const lastStatus = new Map()

  const isChild = (sessionID) => Boolean(sessionID && parents.get(sessionID))

  const sessionOf = (props) =>
    props?.sessionID ?? props?.info?.id ?? props?.part?.sessionID ?? props?.session?.id

  async function send(name, extra = {}) {
    const payload = Buffer.from(
      JSON.stringify({ hook_event_name: name, cwd: directory, ...extra }),
    )
    const env = { ...process.env, PYTHONPATH: REPO }
    try {
      // The file-based hook also rings the terminal bell by printing one. Capture the
      // output and write the bell ourselves, so nothing else can reach the TUI.
      const out = await $`python3 -S ${REPO}/tmux_sentinel/hook.py < ${payload}`
        .env(env)
        .quiet()
        .nothrow()
        .text()
      if (out && out.includes("\x07")) process.stdout.write("\x07")
      await $`python3 -S -m tmux_sentinel_daemon.hook_client < ${payload}`
        .env(env)
        .quiet()
        .nothrow()
    } catch (err) {
      debug({ error: "send failed", name, message: String(err) })
    }
  }

  return {
    event: async ({ event }) => {
      const type = event?.type
      const props = event?.properties ?? {}
      const sessionID = sessionOf(props)
      debug({
        type,
        keys: Object.keys(props),
        sessionID,
        status: props?.status?.type,
        parentID: props?.info?.parentID,
      })

      // Learn which sessions are children before anything else uses the answer.
      if ((type === "session.created" || type === "session.updated") && props.info?.id) {
        parents.set(props.info.id, props.info.parentID ?? null)
      }
      if (isChild(sessionID)) return

      switch (type) {
        case "session.created":
          await send("agentSpawn")
          break

        case "session.status": {
          const status = props.status?.type
          if (status === "busy" && lastStatus.get(sessionID) !== "busy") {
            await send("userPromptSubmit")
          }
          if (status) lastStatus.set(sessionID, status)
          break
        }

        case "session.idle":
          lastStatus.set(sessionID, "idle")
          await send("stop")
          break

        case "session.error":
          // Sets the error flag; the session.idle that normally follows turns it into
          // the error status. The hook model has no separate "failed" event.
          await send("postToolUse", { tool_response: { success: false } })
          break

        // "permission.updated" is the name older releases used.
        case "permission.asked":
        case "permission.updated":
          await send("permissionRequest")
          break

        case "permission.replied":
          await send("permissionReplied")
          break
      }
    },

    // Keeps the session marked as working between status events, and clears a previous
    // result's unseen flag as the next tool starts.
    "tool.execute.before": async (input) => {
      debug({ type: "tool.execute.before", keys: Object.keys(input ?? {}), tool: input?.tool })
      if (isChild(input?.sessionID)) return
      await send("preToolUse")
    },
  }
}
