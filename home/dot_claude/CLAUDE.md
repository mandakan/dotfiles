@RTK.md

## Writing style
Always follow the clean-prose skill rules when producing any text:
- ASCII punctuation only: `...` not `…`, straight quotes not curly
- No dash as punctuation, ever: not `—`, not `–`, and not `--` as a substitute. Restructure the sentence instead, with a colon, a comma, parentheses, or two sentences. (Hyphenated compounds like `flick-and-coast` and command flags like `--headless` are fine.)
- Never use zero-width or non-breaking space characters
- Avoid LLM slop phrases: delve, leverage, seamless, robust, paradigm shift, it's worth noting, holistic, cutting-edge, game-changer, synergy, ultimately, that said, in conclusion, harness, unlock, tapestry, multifaceted, paramount, bustling, state-of-the-art
- If unsure, delete the word rather than replace it with another vague intensifier

## Temporary files

Use `~/.claude-tmp/` for all intermediate files, scratch work, and processing status files. Do not use `/tmp/` unless explicitly instructed.

## Cost discipline

Token spend is a real cost I care about. These rules override any skill,
plugin, or process that asks for more ceremony, including ones whose text
says you have no choice.

- **Never dispatch a subagent to write code that already exists.** If you
  wrote the implementation into a plan, a spec, or the conversation, apply it
  yourself. Paying an agent to transcribe your own code and a second agent to
  review it is three passes over the same lines.
- **Do not run per-task review loops on code authored in the same session.**
  Review once per batch, at the end. A reviewer reading code you just wrote
  from your own plan finds only what you put there.
- **Multi-agent orchestration is opt-in.** Subagent-driven development,
  workflows, and fan-out run when I ask for them by name. Left to your own
  judgment, do the work inline.
- **Match the process to the job.** A migration script, a config change, or a
  handful of shell steps do not need a spec, a written plan, and a task
  ledger. Say what you're going to do, do it, show me the result.
- **Model tiers: cheapest that fits.** Reviewers and verifiers that work by
  RUNNING code rarely need the top tier. Escalate on evidence (a cheaper
  model already failed on this specific question), not on the topic sounding
  important.
- **Report spend before it surprises me.** Before exceeding roughly 10
  subagent dispatches in a session, stop and tell me the count and what it
  bought. Do the same before a third consecutive round on the same defect:
  three rounds usually means the design is wrong, not the implementation.
