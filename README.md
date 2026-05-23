# legistar-mcp

An MCP server that lets your desktop AI agent — Claude Desktop, Claude Code,
Cursor, or any other MCP-compatible client — search NYC City Council
legislation: 19,656 bills, 16,776 hearings, 247 council members. Built
specifically for civic-research workflows where you need not just *whether* a
bill mentions an agency, but the surrounding statutory sentence that
characterizes that agency's role.

## Data source — you supply it

**This package does not ship or fetch legislative data.** It indexes a local
checkout of [`jehiah/nyc_legislation`](https://github.com/jehiah/nyc_legislation),
an open JSON mirror of the NYC Council Legistar API maintained by
[Jehiah Czebotar](https://github.com/jehiah). You provide that checkout when
you run `legistar-mcp index`.

The archive shape (what the indexer walks):

- `introduction/{year}/*.json` — introduced bills
- `resolution/{year}/*.json` — resolutions
- `land_use/{year}/*.json` — land-use applications
- `events/{year}/*.json` — committee hearings + full Council meetings
- `people/*.json` — council members

Clone size: ~2 GB with full history, or ~700 MB with `--depth 1`.

## Requirements

- **Python 3.11+** and **[`uv`](https://docs.astral.sh/uv/)** — uv handles the
  install. It's the only Python toolchain you need to know about.
- **~3 GB free disk** for the archive (~2 GB) + the index (~105 MB).
- **An MCP-compatible AI client** — Claude Desktop, Claude Code, Cursor,
  Continue.dev, etc.

## Quickstart

End-to-end setup in ~3 minutes. Copy-paste, no substitutions needed:

```sh
mkdir -p ~/legistar && cd ~/legistar

# 1. Pull the upstream JSON archive (~700 MB shallow clone)
git clone --depth 1 https://github.com/jehiah/nyc_legislation.git

# 2. Install this server
uv tool install git+https://github.com/WillHsiaoNYC/legistar-mcp

# 3. Build the index (silent for ~80 seconds, then prints stats)
legistar-mcp index --archive ./nyc_legislation --db ./legistar.db
```

You should see:

```
Indexed: bills=19656 events=16776 people=247
```

Final folder layout:

```
~/legistar/
├── nyc_legislation/   ← archive — DO NOT delete; query-time tools read JSON from here
└── legistar.db        ← SQLite index (~105 MB)
```

Now [configure your AI agent](#configure-your-ai-agent) below.

> **Windows note:** the commands work as-is in Git Bash / WSL. In native
> PowerShell, replace `mkdir -p ~/legistar && cd ~/legistar` with
> `New-Item -Force -ItemType Directory $HOME\legistar; cd $HOME\legistar`.
> Everything else is identical.

## Configure your AI agent

The server speaks MCP over stdio. Your agent launches it as a subprocess —
you never start it manually. Add an entry to your agent's MCP config and
restart the agent.

**Replace `/Users/you/legistar/legistar.db` below with the absolute path to
your DB file.** On Linux: `/home/you/legistar/legistar.db`. On Windows:
`C:\\Users\\you\\legistar\\legistar.db`.

### Claude Desktop

Edit `claude_desktop_config.json`:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "legistar": {
      "command": "legistar-mcp",
      "args": ["serve"],
      "env": {
        "LEGISTAR_DB_PATH": "/Users/you/legistar/legistar.db"
      }
    }
  }
}
```

### Claude Code

User-wide: `~/.claude.json`. Project-scoped: `<project>/.mcp.json`. Same
config shape as Claude Desktop. Restart `claude` after editing.

### Cursor / Continue.dev / other MCP clients

Most MCP clients accept the same config shape:

```json
{
  "mcpServers": {
    "legistar": {
      "command": "legistar-mcp",
      "args": ["serve"],
      "env": { "LEGISTAR_DB_PATH": "/Users/you/legistar/legistar.db" }
    }
  }
}
```

Cursor reads `~/.cursor/mcp.json`. For others, check the client's docs for
the config location.

## Verify it works

After restarting your agent, ask:

> Use the legistar MCP to list NYC Council committees with bill and event counts.

Within a few seconds you should get a tabular reply with committees like
"Committee on General Welfare", "Committee on Transportation", etc., each
with bill and event counts. That confirms the server is wired and the index
is populated.

If the tools don't appear at all, jump to [Troubleshooting](#troubleshooting).

## Tools

| Tool | Returns |
|------|---------|
| `search_bills` | Bills matching FTS query + filters (`query`, `agency`, `year_from`, `year_to`, `status`, `type`, `committee`, `sponsor_slug`). Includes role-context `mentions` snippets when `agency` is used. |
| `get_bill` | Full bill record from raw JSON. Lookup by `file` (e.g. `Int 0153-2022`) or numeric `id`. |
| `search_people` | Council members by `name` substring; optional `active_only` filter. |
| `get_person` | Profile by `slug` (e.g. `adrienne-e-adams`) plus sponsored-bill counts grouped by status. |
| `search_events` | Hearings/events by `query`, `agency`, `committee`, `date_from`, `date_to`. Includes per-item `mentions` snippets when `agency` is used. |
| `get_event` | Full event record by numeric `id` — agenda items, minutes notes, votes. |
| `list_committees` | All committees with bill + event counts. Useful for orienting. |

## Example: bills involving the Mayor's Office of Operations

Ask your agent:

> Find NYC Council bills since 2022 that direct the Mayor's Office of
> Operations to do something, and quote the sentence that names them.

The agent calls `search_bills` with `agency="Mayor's Office of Operations"`
and `year_from=2022`. The server resolves the agency name against
`agencies.yaml` aliases, runs FTS5, and attaches a role-context snippet from
each hit's source JSON. A real result for `Int 0153-2022`:

```
...the commissioner of citywide administrative services, in consultation
with the <mark>mayor's office of operations</mark>, shall submit
an annual report...
```

Without the snippet, you'd only know MOO is *mentioned* in the bill. With it,
the agent can characterize MOO's role: *consulted*, *directed*, *reporting
to*, etc. That's the differentiator over a plain title search — the agency
name almost always lives in the bill's statutory text, not its title or
summary.

## Updating

The upstream archive updates near-daily. Pull and re-index:

```sh
cd ~/legistar/nyc_legislation && git pull
cd ~/legistar && legistar-mcp index --archive ./nyc_legislation --db ./legistar.db
```

`--incremental` (the default) skips files whose `LastModified` hasn't changed.
Pass `--full` to rebuild from scratch — useful after a `legistar-mcp` upgrade
or if you suspect index corruption.

Restart your agent after re-indexing so it sees fresh data.

## Troubleshooting

**Tools don't appear in my agent at all.** Check the config file path matches
your client's expected location, and that you restarted the client after
editing. To test the server independently of your agent, run
`legistar-mcp serve` from a terminal with `LEGISTAR_DB_PATH` set — if it
errors, that's the same error your agent saw. You can also drive it
interactively via the MCP Inspector:
`npx @modelcontextprotocol/inspector legistar-mcp serve`.

**`LEGISTAR_DB_PATH is not set` at startup.** Your client config's `env`
block doesn't include `LEGISTAR_DB_PATH`. Add it and restart.

**`LEGISTAR_DB_PATH does not exist` at startup.** The env var is set but
points to nothing. You skipped the index step — run `legistar-mcp index`
first.

**`DB does not record an archive_root`.** The DB file exists but contains
no indexed data (only the schema). Run `legistar-mcp index` against your
archive clone.

**`archive_root recorded in DB does not exist or is not a directory`.** You
moved or deleted the archive clone after indexing. Re-run
`legistar-mcp index --archive <new-path>` to update the recorded location.

**Agency search returns bills but `mentions` is empty for some rows.** FTS5
uses porter stemming — an FTS hit doesn't guarantee a literal phrase match.
The snippet builder needs the literal phrase to highlight. The bill is still
a real match, just without an inline quote. Use `get_bill` to read its full
text.

## Known limitations

- **Agency snippets are built in Python**, not via SQLite's native `snippet()`.
  Contentless FTS5 tables (which we use to keep the DB small) return NULL
  from `snippet()`, so the server reads source JSON at query time and renders
  snippets itself. Tradeoff: ~105 MB DB instead of ~300 MB, at the cost of
  per-result JSON reads and occasional empty `mentions` when porter stemming
  matched a non-literal form.
- **`--incremental` still touches every JSON** to read `LastModified`. It's
  faster than `--full` but the I/O dominates, so the wallclock difference is
  moderate, not dramatic.
- **The archive can't be moved without re-indexing.** The DB stores the
  absolute path to the archive at index time. Move the archive → re-run
  `index`.
- **Read-only.** There are no admin or write tools. Source JSON in your
  archive clone is the source of truth.

## Uninstall

```sh
uv tool uninstall legistar-mcp
rm -rf ~/legistar/
```

Then remove the `legistar` entry from your AI agent's MCP config.

## Credits

- The JSON archive — the actual data this server indexes — is maintained by
  **[Jehiah Czebotar](https://github.com/jehiah)** at
  [`jehiah/nyc_legislation`](https://github.com/jehiah/nyc_legislation),
  generated by his Go client
  [`jehiah/legislator`](https://github.com/jehiah/legislator) against the
  official NYC Council Legistar API. None of this works without that upstream
  — please [star or contribute to it](https://github.com/jehiah/nyc_legislation).
- Source data is public-record NYC Council legislation, retrieved via the
  Granicus-operated [Legistar API](https://webapi.legistar.com/Help).
- `legistar-mcp` is unaffiliated with NYC Council, Granicus, or the upstream
  archive maintainer.

## License

MIT.
