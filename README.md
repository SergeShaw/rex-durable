# rex-durable

This fork adds opt-in support for Codex profiles and a shared Codex daemon.
The upstream behavior remains available when no profiles are configured.

Keep the programs in your [Rex](https://www.superlogical.com/rex) tabs across server restarts and reboots.

Rex saves your sessions, tabs, and splits when its server stops, and restores them when it starts again. The programs that were running in them don't come back: every tab returns as an empty shell. rex-durable remembers what was running in each tab and puts it back.

| Running in the tab | After a restart |
| --- | --- |
| [fx](https://fx.sh) chat | Reopens on its conversation: `fx --resume <id>` |
| Claude Code chat | Reopens on its conversation: `claude --resume <id>` |
| Codex chat | Reopens on its conversation: `codex resume <id>` |
| Any other program | Typed at the prompt, not run. Press Enter if you want it. |
| Plain shell | Left alone |

Chats keep their options, such as `--model`, and come back in the same tab, split, and folder.

## Install

```sh
git clone https://github.com/fazxes/rex-durable
cd rex-durable
./install.sh --add-to-config
```

The installer:

1. Finds Rex's `rex` command. On macOS it uses the copy Rex keeps up to date in `~/Library/Application Support/com.superlogical.rex/RexServer/Versions/Current/`, so Rex updates don't break it.
2. Copies `durable.lua` to `~/Library/Application Support/rex-durable/` on macOS, or `~/.local/share/rex-durable/` on Linux.
3. Starts it as a login service: a launchd agent on macOS, a systemd user service on Linux.
4. Adds command palette actions (`rex_durable.lua` in your Rex config folder). `--add-to-config` also loads them from `init.lua` for you. Without it, the installer prints the one line to add.

Run `./install.sh` again to update. Use `--no-service` to install the files without the service, or `REX=/path/to/rex ./install.sh` to choose the `rex` command.

Requirements: Rex for Mac, or the Rex server on Linux, recent enough to have the Lua API's `rex.exec`, `rex.json`, `rex.every`, and `rex.on`. The installer checks this. You also need `lsof`, or `/proc` on Linux.

## Using it

There's nothing to do day to day. Every 30 seconds, and a few seconds after any change, rex-durable saves what runs in each tab. After a restart, it brings everything back within seconds and shows a notification such as "Reopened 12 chats."

To check on it, or to do something by hand:

```sh
S="$HOME/Library/Application Support/rex-durable/durable.lua"   # Linux: ~/.local/share/rex-durable/durable.lua

rex do "$S" mode=status                    # what is saved, and the last restore
rex do "$S" mode=save                      # save now
rex do "$S" mode=reopen dry_run=true       # show what a reopen would type
rex do "$S" mode=reopen                    # reopen programs in their tabs
rex do "$S" mode=checkpoint name=before-update
rex do "$S" mode=reopen from=before-update # reopen from a pinned checkpoint
rex do "$S" mode=rebuild from=before-update yes=true  # recreate saved sessions
```

The command palette has the same actions: search for "durable".

Add `programs=false` to any mode to reopen chats only, and never type other programs' commands.

## How it works

`durable.lua` is a [Rex Lua script](https://www.superlogical.com/rex/docs/automate/lua-scripts). The service runs it with `rex do durable.lua --all`, which hears events from every session. It uses only Rex's public API:

- **Watching.** On a timer (`rex.every`) and after layout and terminal events (`rex.on`), it starts a short `rex do durable.lua mode=save`. Each save runs on its own connection, so reading every session never affects the watcher.
- **Saving.** For each tab, it records the block ID, the folder, and the program from the terminal's `process` method. It then works out which conversation each chat is on:
  - **fx** holds a lock file for its session (`~/.fx/sessions/v2/<id>/lock`).
  - **Codex** keeps its rollout file open (`~/.codex/sessions/…/rollout-…-<id>.jsonl`), or the ID is on its command line (`codex resume <id>`).
  - **Claude Code** names the session on its command line (`--resume <id>`). Otherwise rex-durable uses the newest session file for that folder written since the chat started. If two such chats share a folder, it can't tell them apart, so they come back typed, not run.

  Saves go to `~/Library/Application Support/rex-durable/` on macOS, or `~/.local/state/rex-durable/` on Linux. It keeps the last save of each of the latest 10 servers.
- **Restoring.** When the watcher connects to a server instance it hasn't seen, it takes the last save from the previous server. Rex restores its own sessions with the same block IDs, so each program goes back into exactly the tab it came from, by block ID first and then by session name and position. If the server starts empty, for example after `rex server stop --discard`, rex-durable creates the saved sessions itself.

## Safety

- It only types into a tab that is sitting at an idle shell. A tab where something is running is left alone.
- It avoids opening a chat already detected in a Rex pane. For ordinary file-backed clients it also checks external holders of the session file. Profile-mode Codex chats are deduplicated across Rex panes only: a shared daemon can hold a session file without any terminal viewing that chat.
- It acts once per server start. Running it again on the same server only saves.
- Programs other than chats are typed, never run.
- Saves older than 30 days are never restored on their own.
- `dry_run=true` shows what a reopen would type, without typing it.

## Limits

- A chat that was in the middle of a reply reopens, but the reply in progress is gone, so re-ask its last step. Commands a chat had running in the background don't come back.
- Without explicit Codex profiles, only chats in your own home folder's profile are reopened. An fx chat run with a different `HOME`, such as a test profile, comes back typed, not run.
- It works with the server it runs next to. For a remote Rex server, install rex-durable on that machine.
- A sudden power cut loses at most about the last 30 seconds of changes.

## Codex profiles and shared daemons

For a separate `CODEX_HOME`, create `config.json` in rex-durable's state
directory (on macOS, `~/Library/Application Support/rex-durable/`):

```json
{
  "programs": false,
  "codex_profiles": [
    {
      "home": "/absolute/path/to/codex-profile",
      "launcher": "/absolute/path/to/codex-launcher"
    }
  ]
}
```

Use absolute paths, resolving directory symlinks in the profile home. The launcher is one executable, such as a wrapper that
starts your work Codex configuration; it is not a shell command. A restore
explicitly sets `CODEX_HOME` and invokes that launcher with `resume <id>`.
The local configuration and saved chat IDs are not part of this repository.

Include `thread-id` in that Codex profile's `tui.terminal_title`, for example
`["thread-title", "thread-id", "project"]`. Rex must expose the title's owner.
The TUI must have a profile log database or an argv-lock file open, so the
profile can be established independently of the title. `sqlite3` with JSON
output support must be installed.

In this mode, every Codex pane requires a configured profile and a title owned
by its current Codex process. A full or truncated UUID (at least 24 characters)
must match exactly one unarchived conversation in that profile's newest
`state_*.sqlite` index, with the same working folder. Only IDs and file/folder
metadata are queried, read-only; conversation content is not read. Changing
chats updates the saved ID through Rex's title-change event. Missing or
ambiguous identities are saved as unidentified and never launched, even if
the original command names an older conversation. Older snapshots without
verified profile metadata cannot bypass profile mode.

Shared-daemon Codex chats are deduplicated within the connected Rex server.
This mode cannot determine whether the same chat is also open in another
terminal application. It does not interpret a daemon holding the rollout as
an active terminal. A restored conversation is reopened; interrupted work is
not automatically submitted again.

Install from a checked-out commit to pin the code. The installer records that
commit in `REVISION` beside the installed script. Updates are manual. To roll
back, check out the previous tested commit and rerun the installer; local
configuration and snapshots are retained.

## Uninstall

```sh
./uninstall.sh          # stop the service and remove the files, keep your saves
./uninstall.sh --purge  # remove the saves too
```

Your Rex sessions and the programs in them are not touched. If you added `require("rex_durable")` to `init.lua`, remove that line too.

## Development

```sh
test/e2e.sh            # unit tests, then end-to-end tests on a throwaway Rex server
test/e2e.sh --unit     # unit tests only
lua5.1 test/platform.lua   # process and file helpers on Linux, without Rex
python3 test/codex_profiles.py  # shared-daemon fixtures, separate Rex server
python3 test/real_codex.py      # installed Codex, disposable profile, localhost-only endpoint
```

The end-to-end test needs Rex and a C compiler. It runs its own Rex server on its own socket, with fake fx, Claude Code, and Codex chats in a throwaway home folder. It never connects to your Rex server and never touches your real chats.

The opt-in real-Codex test needs Python 3.8+ and Codex CLI with shared-daemon
support (validated with 0.169.0). It creates two disposable conversations
against a local endpoint that returns a fixed error, opens their real TUIs,
restarts only its separate Rex server, and checks both conversation IDs and
the unchanged Codex daemon PID. It then repeats with both Rex and the test
Codex daemon stopped, checking cold-daemon recovery. No work configuration or credentials are
copied, and no AI service is called.

rex-durable is not made by or affiliated with Superlogical.

## License

[MIT](LICENSE)
