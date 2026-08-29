---
name: play-publish
description: Publish Android App Bundles to Google Play from any repo — upload an .aab to a track as a draft, then promote it to testers — plus first-time setup of the service account and installing the tooling into a project. Use this whenever the user mentions publishing or shipping an Android app, Play Console, Play Store, internal testing, closed or open testing tracks, alpha/beta/production tracks, rolling out a release to testers, uploading an .aab or app bundle, "get this on my phone via Play", Play Developer API or androidpublisher, service accounts for Play, versionCode conflicts, or automating Android releases in CI — even when they don't name Play explicitly, e.g. "how do I get testers on this build" or "ship this to my test group".
---

# Publishing to Google Play

Getting an Android build to testers through Play is a small API surface wrapped
in a set of rules that are easy to discover the hard way. This skill carries
the rules and a dependency-free client so you don't rediscover them per repo.

`scripts/play_publish.py` is the canonical copy. Run it directly for a one-off,
or install it into a repo when CI or the user needs to publish without you.

## The rules that actually bite

Read these before doing anything. Most Play publishing failures are one of
them, and the error messages point somewhere unhelpful.

**The first bundle for a package must be uploaded by hand.** The API cannot
create a listing, and it refuses a package it has never seen — you get a 404
that reads like a wrong URL. If the app is new, the user has to create it in
Play Console and upload one bundle manually. Everything after that automates.
Check with `--dry-run` before promising automation.

**versionCode must strictly increase and is never reusable.** Not per track —
per package, forever. Uploading a code that already exists anywhere fails. Run
`--dry-run` first and read the existing codes off the tracks; that is the
cheapest way to know what the next build must be. Bumping it usually means
bumping the project's version, which is the project's own concern, not this
skill's.

**Play only takes App Bundles, and only release-signed ones.** An APK is
rejected, and so is a debug-signed bundle — on every track including internal,
because Godot and Gradle debug exports set the debuggable flag.

**Tracks live inside an edit.** There is no top-level tracks endpoint. Reading
a track means opening an edit, reading, and discarding the edit. Getting this
wrong returns an HTML 404 rather than a JSON error.

**One live release per track.** Promoting a draft means sending it as the
track's *only* release, superseding what was live. Sending both is rejected.

**Edits are transactional.** Open, act, commit. Nothing is visible until the
commit lands, so a failure part-way leaves the track untouched. That is why a
half-finished run is safe to simply retry.

## Publishing

Default to uploading as a **draft**, then promoting as a separate step. The
draft is not merely a safety net — it is the seam where a human decides that
this build is the one testers get. Collapsing the two into one command removes
that decision, so only do it when the user asks.

```bash
python3 <skill>/scripts/play_publish.py --dry-run                      # what's on the tracks now
python3 <skill>/scripts/play_publish.py --aab build/app.aab --track internal
python3 <skill>/scripts/play_publish.py --promote --track internal     # roll out to testers
```

Verify by reading the track back with `--dry-run` rather than trusting the exit
code. The upload printing success and the release actually being where you
think are different claims.

Config resolves environment-first, then the nearest `play.properties` found
walking up from the working directory:

| Setting | Environment | play.properties |
|---|---|---|
| Service account key | `PLAY_SERVICE_ACCOUNT` | `serviceAccountFile` |
| Package name | `PLAY_PACKAGE` | `packageName` |

`--check` validates config without touching the network. `--dry-run`
authenticates and lists tracks without changing anything.

## Handling the service account key

The key grants publishing rights to the entire Play account — every app on it,
up to and including production. Treat it like the upload keystore:

- It lives **outside the repo**, conventionally alongside other signing
  material (e.g. `~/keystores/<project>-play-api.json`), mode `600`.
- `play.properties` only points at it, and is **gitignored**.
- Never print its contents, never paste it into a commit, an issue or a PR
  body. Validating it means reading `client_email` and `project_id`, not the
  private key.
- If a user offers it inline in chat rather than as a path, take the path
  instead and tell them why.

For setup — creating the service account, linking Play Console to Google
Cloud, granting release permission — read `references/setup.md` and walk the
user through it. Those steps are theirs to perform; you cannot do them.

## Installing into a repo

When the user wants CI or standalone publishing, install rather than run from
here. Copy the script in and wire it up:

1. `scripts/play_publish.py` → the repo's tooling directory (`tools/`,
   `scripts/`, whatever the repo already uses — match it, don't impose).
2. `assets/play.properties.example` → repo root, adjusted to the project.
3. Add `play.properties` to `.gitignore`, and confirm with `git check-ignore -v
   play.properties` rather than assuming.
4. Add targets matching the repo's build system. `assets/make-targets.mk` has
   Make ones; translate for npm scripts, justfile, or whatever is in use.
5. Document it where the repo documents its other build steps.

Note in the repo's docs that the skill holds the canonical copy, so a later fix
here can be re-synced rather than diverging silently.

## Wiring it to a release process

Where the versionCode comes from is the project's business, but it has to
increase, so the ordering matters: **cut the version bump first, then build,
then upload.** Building before the bump produces a stale versionCode and Play
rejects it after the build has already run.

If the repo uses release-please or similar, that means merging the release PR
before building — the version file is the input to the build, not an
afterthought.

## Troubleshooting

`references/troubleshooting.md` maps the failures to causes: what a 401 means
versus a 403, why a 404 usually means "never uploaded by hand", versionCode
collisions, and why an HTML error body means the URL was wrong rather than the
request. Read it when a run fails rather than guessing from the status code.
