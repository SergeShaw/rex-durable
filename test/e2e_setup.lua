-- End-to-end test setup: two sessions with fake chats, a program, and a
-- shell. Run by test/e2e.sh as: rex do test/e2e_setup.lua bin=DIR dir=DIR

local bin, dir = rex.args.bin, rex.args.dir
local SHELL = "com.superlogical.terminal.shell"

local function q(word) return "'" .. word:gsub("'", "'\\''") .. "'" end
local function tab(command)
  local options = { cwd = dir }
  if command then options.initial_input = command .. "\n" end
  return { block = { flavor = SHELL, options = options } }
end

assert(rex.call("session.create", { label = "work", initial_windows = {
  { layout = { split = { direction = "horizontal", ratio = 0.5,
    before = tab(q(bin .. "/fx") .. " --resume FXA"),
    after = tab(q(bin .. "/codex") .. " resume 11111111-2222-4333-8444-555555555555") } } },
  { layout = tab(q(bin .. "/claude")) },   -- a new conversation, found by its file
  { layout = tab("sleep 4242") },          -- another program
  { layout = tab(nil) },                   -- a plain shell
  { layout = tab(q(bin .. "/fx") .. " --resume FXC") },
} }))
assert(rex.call("session.create", { label = "side", initial_windows = {
  { layout = tab(q(bin .. "/fx") .. " --resume FXB") },
} }))
return "ok"
