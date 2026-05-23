# legistar-mcp

An MCP server over the NYC Legistar legislation archive. Ask Claude civic-research
questions in natural language and have it search across 19,656 bills, 16,776
hearings, and 247 council members — including bills that mention specific city
agencies, which is the canonical use case this server was built to solve.

## What it does

`legistar-mcp` indexes a local checkout of the
[NYC Council legislation archive](https://github.com/NewYorkCityCouncil/nyc_legislation)
into a SQLite + FTS5 database and exposes 7 read-only MCP tools over stdio.
Detail-fetch tools read the underlying JSON directly so they never drift from
the source.

The headline example: find every bill since 2022 that directs the Mayor's
Office of Operations to do something, and surface the sentence that names them.
See [Example](#example-find-bills-involving-the-mayors-office-of-operations).

## Quickstart

### 1. Prerequisites

- Python 3.11+ and [`uv`](https://docs.astral.sh/uv/)
- A local clone of `NewYorkCityCouncil/nyc_legislation`:

```sh
git clone https://github.com/NewYorkCityCouncil/nyc_legislation.git
```

### 2. Install

```sh
uv tool install legistar-mcp
```

This puts `legistar-mcp` on your `PATH`.

### 3. Build the index (one-time)

Point `--archive` at your clone and `--db` at where you want the SQLite file:

```sh
legistar-mcp index \
  --archive /path/to/nyc_legislation \
  --db /path/to/legistar.db
```

On a typical dev machine this takes around 80 seconds and emits:

```
Indexed: bills=19656 events=16776 people=247
```

The DB ends up around 400 MB. `--archive` defaults from `LEGISTAR_ARCHIVE_PATH`
and `--db` defaults from `LEGISTAR_DB_PATH` if you'd rather set those in the
environment.

### 4. Configure Claude Desktop

Add the following to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "legistar": {
      "command": "legistar-mcp",
      "args": ["serve"],
      "env": {
        "LEGISTAR_DB_PATH": "/path/to/legistar.db"
      }
    }
  }
}
```

Note: `serve` only needs `LEGISTAR_DB_PATH`. The archive root is read from the
DB itself (it was recorded there at index time), so you don't have to keep two
paths in sync.

Restart Claude Desktop and ask a civic-research question to confirm the tools
are visible.

## Tools

| Tool | What it does |
|------|--------------|
| `search_bills` | FTS over Name/Title/Summary/Text plus filters: `query`, `agency`, `year_from`, `year_to`, `status`, `type`, `committee`, `sponsor_slug`. Returns role-context snippets when `agency` is used. |
| `get_bill` | Fetch a single bill's full record by `file` (e.g. `Int 0153-2022`) or numeric `id`. |
| `search_people` | Find council members by `name`, optionally filtered to currently active members. |
| `get_person` | Full profile by `slug`, including sponsored-bill counts. |
| `search_events` | Hearings/events by `query`, `agency`, `committee`, and `date_from`/`date_to`. |
| `get_event` | Single event by numeric `id`, including agenda items and minutes notes. |
| `list_committees` | All committees with bill and event counts. |

## Example: find bills involving the Mayor's Office of Operations

Ask Claude:

> Find NYC Council bills since 2022 that direct the Mayor's Office of Operations
> to do something, and quote the sentence that mentions them.

Claude will call `search_bills` with `agency="Mayor's Office of Operations"` and
`year_from=2022`. The agency resolver expands that to an FTS query against the
aliases in `agencies.yaml`, and the server attaches a role-context snippet for
each hit. A real result for `Int 0153-2022` looks like:

```
...the commissioner of citywide administrative services, in consultation
with the <mark>mayor's office of operations</mark>, shall submit
an annual report...
```

That snippet is what makes this server useful: the agency name is buried in
bill `Text`, not the title or summary, so a naive title search would miss it
entirely.

## Updating

When the upstream archive changes, pull and re-index. `--incremental` (the
default) skips bills whose `LastModified` hasn't changed:

```sh
cd /path/to/nyc_legislation && git pull upstream master
legistar-mcp index \
  --archive /path/to/nyc_legislation \
  --db /path/to/legistar.db
```

Pass `--full` to force a from-scratch rebuild.

## Troubleshooting

**`LEGISTAR_DB_PATH is not set` at startup.** You launched `serve` without the
env var. Set it in your Claude Desktop config under `env` or your shell.

**`archive_root recorded in DB does not exist or is not a directory`.** You
moved or renamed the archive after indexing. Re-run `legistar-mcp index` to
update the recorded path.

**`search_bills(agency=...)` returns bills but `mentions` is empty.** FTS uses
the porter stemmer, so an FTS hit doesn't guarantee a literal phrase match.
The Python-side snippet builder needs a literal substring to highlight. The
bill is still a real match — just without an inline quote.

## Known limitations

- Agency snippets are built in Python by re-reading source JSON. Because FTS
  uses porter stemming, some FTS hits won't yield a literal-phrase snippet and
  `mentions` will be empty for those rows.
- `--incremental` still reads every JSON file to compare `LastModified`. It's
  faster than a full rebuild but the I/O dominates, so the wallclock difference
  is modest.
- The DB's recorded `archive_root` is whatever path you passed to `index` last.
  If you move the archive without re-indexing, `get_bill`/`get_event` detail
  fetches will fail until you re-run `index`.
- Read-only. There are no write or admin tools. Bill JSON in your fork is the
  source of truth.

## License

MIT.
