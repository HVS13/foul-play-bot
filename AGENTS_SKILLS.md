# CodexGPT Skills for this repo

This file captures lightweight, repeatable playbooks for common tasks.

## Skill: foul-play-cli
Use when adding or changing CLI flags, bot modes, or run flow.
- Update `fp/config.py` for arguments and validation.
- Wire orchestration in `fp/main.py`.
- Implement local battle behavior through small hooks plus `fp/custom/` helpers.
- Update `README.md` with new flags and examples.
- Run `ruff check`, `ruff format --check --diff`, and `pytest tests`.

## Skill: resume-battle-maintenance
Use when touching resume/attach or reconnect behavior.
- Review the shared `attach_to_battle()` path in `fp/run_battle.py`.
- Review protocol assumptions in `fp/battle/protocol.py`.
- Preserve session telemetry when replacing a reconstructed `Battle` object.
- Always use the latest request/rqid before sending a post-reconnect choice.
- Never blindly resend a battle choice that failed because the websocket disconnected.

## Skill: search-policy-customization
Use when changing local move-selection behavior.
- Keep upstream mode preparation in `fp/modes/` intact.
- Keep the public `find_best_move` compatibility path.
- Use `SearchResult` from `fp/custom/decisions.py` for local consumers.
- Put decision classification in one place; do not recreate setup/protect/pivot tagging in search or telemetry modules.
- Preserve upstream `search_threads`, team-preview search settings, and poke-engine call signatures.

## Skill: dashboard-overlay
Use when changing the GUI or overlay.
- Treat `fp/custom/events.py` as the interface contract.
- Keep the dashboard read-only unless a future design explicitly adds authenticated controls.
- Keep default binding on `127.0.0.1`.
- Avoid adding a web framework unless the standard-library server can no longer meet a concrete requirement.
- Keep battle/search code unaware of HTML/CSS details.

## Skill: upstream-sync
Use when updating from `pmariglia/foul-play`.
- Work on a `sync/upstream-*` branch, not directly on `main`.
- `git fetch upstream`
- `git merge upstream/main`
- Resolve structural conflicts in favor of current upstream architecture.
- Reapply only the small hooks needed for `fp/custom/`, config, attach/reconnect, and policy behavior.
- Run CI before merging the sync branch into `main`.
