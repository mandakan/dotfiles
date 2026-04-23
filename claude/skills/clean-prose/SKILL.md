---
name: clean-prose
description: >
  Enforce slop-free, plain prose. Use this skill proactively whenever writing
  or editing any text — wiki pages, responses, summaries, documents. Also use
  it reactively when the user says things like "check for slop", "clean this
  up", "remove em dashes", "review for LLM patterns", "is this slop-free", or
  any request to audit or fix text quality. Prefer triggering too eagerly over
  missing a case.
---

# Clean Prose — Slop-Free Writing Rules

Two modes: **write clean** (active while generating any prose) and **review & fix** (when asked to audit existing text).

---

## Typography: ASCII-first

Always use plain ASCII punctuation. Never use the typographic variants.

| Use this | Never this | Notes |
|---|---|---|
| `--` or `-` | `—` (U+2014 em dash) | |
| `-` | `–` (U+2013 en dash) | |
| `...` | `…` (U+2026 ellipsis) | |
| `"` | `"` `"` (U+201C/201D curly double quotes) | |
| `'` | `'` `'` (U+2018/2019 curly single quotes) | |
| ` ` (regular space) | ` ` ` ` (non-breaking spaces) | |
| delete | `​` `‌` `‍` `⁠` `﻿` (zero-width chars) | |

The reason: typographic characters are a reliable LLM fingerprint. They also cause diffs to look dirty, break search, and embed invisible state in files.

---

## Phrases to avoid

These words and phrases are overrepresented in LLM output to the point of being tells. Avoid them. Where a substitute is needed, pick the plainest word that fits.

**The core list:**
- delve / delves / delved / delving → just say "look at", "examine", "explore"
- it's worth noting → delete or restate as a plain claim
- tapestry of → describe the thing directly
- leverage (as a verb) → "use", "apply", "draw on"
- unlock → just say what the thing enables
- harness → "use", "apply"
- embark on a journey → describe what actually happens
- in today's fast-paced world → delete
- in conclusion → delete; just conclude
- that said → usually delete; if keeping, rewrite as a plain sentence
- ultimately → usually delete or find a more specific word
- seamless / seamlessly → describe the actual quality instead
- robust → describe what makes it strong
- holistic → describe what dimensions are included
- cutting-edge → describe what's new
- paradigm shift → describe what changed and why
- game-changer → describe the actual impact
- synergy → describe what actually combines with what
- state-of-the-art → describe what's current
- multifaceted → describe the specific facets
- paramount → "most important", "essential", or restructure the sentence
- bustling → describe the actual activity

**The deeper principle:** LLM slop substitutes vague intensity words for concrete description. If a sentence would be improved by deleting a word entirely, delete it. If a sentence sounds like it belongs in a corporate white paper or an AI-generated blog post, rewrite it.

---

## Review & Fix Workflow

When asked to review or clean text for slop:

1. **Scan** the target text or file for:
   - Any typographic characters from the table above
   - Any phrase patterns from the list above
   - Any other constructions that read as LLM-generated filler (excessive hedging, abstract intensifiers, bureaucratic rhythm)

2. **Fix** in place:
   - Replace typographic characters with their ASCII equivalents
   - Rewrite or delete flagged phrases — don't just swap one slop word for another
   - Preserve the author's meaning and voice; change only what breaks the plain-prose standard

3. **Report** briefly:
   - How many typographic fixes were made (by type)
   - Which phrases were rewritten and how
   - Any cases where you flagged something but left it because removing it would change meaning

Keep the report short. The goal is clean text, not an audit trail.

---

## What this is not

This is not a style guide about sentence length, paragraph structure, or formality level. It is specifically about the mechanical markers that identify LLM-generated text — invisible characters, typographic substitutions, and a short list of overused phrases that have become fingerprints.

Plain prose can still be precise, technical, and sophisticated. These rules only remove the tells.
