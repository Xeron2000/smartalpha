# data/research — gitignored

This directory is intentionally gitignored (`data/research/**` in `.gitignore`).

- Real research outputs (memory, leaderboard, reviews, redteam, runs, benchmark) are
  Evidence lineage and must be regenerated via `smartalpha research cycle`.
- Do not commit `+0.42 SOL` / `PROMISING` fixtures as if they were real results.
- Fixtures for tests live in `tests/fixtures/research/` and are explicitly allowed.

To reproduce:
```bash
uv run smartalpha research cycle --dry-run   # fixture, always runnable
uv run smartalpha research cycle             # live, requires GMGN_API_KEY + real discovery data
```
