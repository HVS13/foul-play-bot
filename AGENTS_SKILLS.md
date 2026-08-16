# CodexGPT Skills for this repo

This file captures lightweight, repeatable playbooks for common tasks.

## Skill: foul-play-cli
Use when adding or changing CLI flags, bot modes, or run flow.
- Update `fp/config.py` for arguments and validation.
- Wire orchestration in `fp/main.py`.
- Implement battle behavior in `fp/run_battle.py` or isolated local helpers.
- Update `README.md` with new flags and examples.
- Run `ruff check`, `ruff format --check --diff`, and `pytest tests`.

## Skill: resume-battle-maintenance
Use when touching resume/attach or reconnect behavior.
- Review `fp/run_battle.py` state rebuild and reconnect flow.
- Review protocol assumptions in `fp/battle/protocol.py`.
- Keep telemetry fields in `fp/battle/state.py` compatible with deep copies.
- Validate `--battle-tag` and `--battle-url` parsing in `fp/config.py`.

## Skill: search-policy-customization
Use when changing local move-selection behavior.
- Keep upstream mode preparation in `fp/modes/` intact.
- Put policy/risk behavior in `fp/search/main.py` or an isolated helper.
- Preserve upstream `search_threads`, team-preview search settings, and poke-engine call signatures.
- Default behavior should remain close to upstream when `--risk-mode balanced` is used.

## Skill: upstream-sync
Use when updating from `pmariglia/foul-play`.
- Work on a `sync/upstream-*` branch, not directly on `main`.
- `git fetch upstream`
- `git merge upstream/main`
- Resolve structural conflicts in favor of current upstream architecture.
- Reapply only the small local hooks needed for `fp/custom_features.py`, config, resume, reconnect, and policy behavior.
- Run CI before merging the sync branch into `main`.
