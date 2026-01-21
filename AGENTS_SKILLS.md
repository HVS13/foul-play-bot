# CodexGPT Skills for this repo

This file captures lightweight, repeatable playbooks for common tasks.

## Skill: foul-play-cli
Use when adding or changing CLI flags, bot modes, or the run flow.
- Update `config.py` for args and validation.
- Wire mode logic in `run.py`.
- Implement battle behavior in `fp/run_battle.py`.
- Update `README.md` with new flags and examples.
- Run `python -m compileall .` after changes.

## Skill: resume-battle-maintenance
Use when touching the resume/attach flow or battle log parsing.
- Review `fp/run_battle.py` resume logic and log catch-up.
- Keep guards in `fp/battle_modifier.py` for request timing.
- Validate that `--battle-tag`/`--battle-url` handling remains correct.

## Skill: upstream-sync
Use when updating your fork from the original repo.
- `git fetch upstream`
- `git merge upstream/main`
- `git push origin main`
- If upstream uses `master`, merge `upstream/master` instead.
