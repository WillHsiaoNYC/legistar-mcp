# legistar-mcp

An MCP server that lets your desktop AI agent — Claude Desktop, Claude Code,
Cursor, or any other MCP-compatible client — search NYC City Council
legislation: 21,270 bills, 17,225 hearings, 253 council members. Built
specifically for civic-research workflows where you need not just *whether* a
bill mentions an agency, but the surrounding statutory sentence that
characterizes that agency's role.

## What you can ask

Once installed, paste any of these into your AI agent:

> *"Find council bills from the past 2 years involving the Mayor's Office of
> Operations — with links and a quote characterizing MOO's role in each."*
> → The bills, plus a snippet of statutory text so you can tell whether MOO
> is *directed*, *consulted*, or *reporting to*.

> *"What is Councilmember XYZ's voting history this year?"*
> → Every roll-call vote they cast, with the bill, date, and outcome.

> *"Give me upcoming City Council hearings in the next 6 months involving
> NYPD, with links."*
> → A dated list of scheduled hearings with the agenda items that mention
> NYPD, each linking to the official Legistar record.

**Every result links to the official Legistar record.** You can verify any
claim the agent makes against the source.

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

**Your AI has to be running on this computer** — a desktop app or a terminal-based tool. Web AIs (claude.ai, chatgpt.com) run in the cloud and can't install things on your machine; [skip to manual install](#manual-install) if that's you.

Two paths — pick the one that matches you:

<table>
<thead>
<tr>
<th>🖥️ I use my AI as a desktop app on this computer</th>
<th>⌨️ I'm a developer using my AI in a terminal</th>
</tr>
</thead>
<tbody>
<tr>
<td>Claude Desktop · ChatGPT (desktop app) · Cursor · etc.</td>
<td>Claude Code · Codex CLI · etc.</td>
</tr>
<tr>
<td>
<ol>
<li>Open a new chat in your AI app.</li>
<li>Paste the message below.</li>
<li>Click <strong>Allow</strong> each time your AI asks for permission.</li>
</ol>
</td>
<td>
<ol>
<li>Start your AI in the terminal.</li>
<li>Paste the message below.</li>
<li>Press <strong>y</strong> to allow each step.</li>
</ol>
</td>
</tr>
</tbody>
</table>

**The message to paste:**

```
Install the MCP server at https://github.com/WillHsiaoNYC/legistar-mcp
on this machine — follow its README to clone the data archive, install
the package, build the index, and wire it into my MCP client config.
Then run a verification query to confirm it works.
```

> [!TIP]
> **What "done" looks like:** your AI reports something like `Indexed: bills=21270 events=17225 people=253` and confirms it's connected to your AI app. Total time ~3 minutes, most of it the 700 MB data download. After that, restart your AI app and start asking questions.

> [!WARNING]
> **If you only use AI through a website** (claude.ai, chatgpt.com), it can't install things on your computer — use the manual install below.

<details id="manual-install">
<summary><strong>Manual install</strong> — for website-only AI users, or if you'd rather run the commands yourself</summary>

Three commands to run in your terminal. Copy-paste, no substitutions needed:

```sh
mkdir -p ~/legistar && cd ~/legistar

# 1. Pull the upstream JSON archive (~700 MB shallow clone)
git clone --depth 1 https://github.com/jehiah/nyc_legislation.git

# 2. Install this server
uv tool install git+https://github.com/WillHsiaoNYC/legistar-mcp

# 3. Build the search index (~80 seconds)
legistar-mcp index --archive ./nyc_legislation --db ./legistar.db
```

You should see:

```
Indexed: bills=21270 events=17225 people=253
```

Final folder layout:

```
~/legistar/
├── nyc_legislation/   ← archive — DO NOT delete; query-time tools read JSON from here
└── legistar.db        ← SQLite index (~105 MB)
```

Now [configure your AI agent](#configure-your-ai-agent) below using the per-client JSON snippets.

> **Windows note:** the commands work as-is in Git Bash / WSL. In native PowerShell, replace `mkdir -p ~/legistar && cd ~/legistar` with `New-Item -Force -ItemType Directory $HOME\legistar; cd $HOME\legistar`. Everything else is identical.

</details>

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
| `aggregate_bills` | Per-group counts when bills are grouped by one or more dimensions (`status_name`, `type_name`, `body_name`, `sponsor_slug`, `intro_year`). Same filter surface as `search_bills`. |
| `list_vocabulary` | Distinct non-null values for a known column (`status_name`, `type_name`, `body_name`, `event_committee`) — discover the exact spelling of statuses, types, and committees. |
| `recent_bills` | Bills introduced within the last `days` days. Convenience wrapper; for agency-scoped searches use `search_bills(agency=...)`. |
| `upcoming_events` | Events scheduled in the next `days` days. Optional `committee` body_name filter. |
| `co_sponsors` | Council members who have co-sponsored the most bills with a given person (`slug`). Returns slug, full_name, and overlap_count. |
| `get_bill_hearings` | Events where a given bill was on the agenda (lookup by `file` like `Int 0153-2022` or numeric `id`). Set `only_upcoming=True` to filter to future events. |
| `get_event_bills` | Bills on the agenda for a specific event, sorted by agenda sequence. |
| `get_voting_record` | Every vote a council member (`slug`) has cast — filter by `year_from`/`year_to` and `vote_value` (e.g., 'Affirmative', 'Negative', 'Absent'). |
| `vote_breakdown` | Every council member's vote on a specific bill (`bill_id`); sorted most-recent first with NULL-date rows last; bound result with `limit` (default 100). |

## How agency role-context works

This is the differentiator behind the MOO example up top. When you ask about
an agency, the agent calls `search_bills` with `agency="Mayor's Office of
Operations"` and a year filter. The server resolves the agency name against
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

## Update the index

The upstream archive gets new bills, hearings, and votes on most weekdays. Two
commands re-sync everything:

```sh
cd ~/legistar/nyc_legislation && git pull
cd ~/legistar && legistar-mcp index --archive ./nyc_legislation --db ./legistar.db
```

Output: `Indexed: bills=N events=N people=247` where `N` is how many files
changed since your last index — a few seconds when nothing changed, ~30s on
busy days, ~80s with `--full`. Default is `--incremental`; pass `--full` to
rebuild from scratch.

**Upgrading the package?** Schema-bumping releases (anything that adds new
tables or columns) require `--full` to backfill the new data across the
whole archive. The indexer will refuse `--incremental` and tell you to
re-run with `--full` until your DB schema is current. Don't ignore the
message — your tools will silently return partial results otherwise.

Restart your AI agent to pick up the new data.

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
