---
name: play-publish
description: Publish Android App Bundles to Google Play from any repo — upload an .aab to a track as a draft, then promote it to testers — plus first-time setup of the service account and installing the tooling into a project. Use this whenever the user mentions publishing or shipping an Android app, Play Console, Play Store, internal testing, closed or open testing tracks, alpha/beta/production tracks, rolling out a release to testers, uploading an .aab or app bundle, "get this on my phone via Play", Play Developer API or androidpublisher, service accounts for Play, versionCode conflicts, automating Android releases in CI, or updating a Play listing's copy, description or screenshots — even when they don't name Play explicitly, e.g. "how do I get testers on this build" or "ship this to my test group".
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

**The listing's default language cannot be changed through the API.** The
`edits.details` write is accepted and the *commit* then fails with a 403, so it
looks like a permissions problem and is not one. Adding a second language and
filling it works fine; making it the default is a Play Console setting only.
Do not spend an afternoon on service-account roles chasing this.

**Listing images are per language.** They cannot be copied between languages
server-side, so adding a locale means re-uploading every slot into it. Budget
for that before offering to move a listing.

**A listing is not per-track.** Title, descriptions and images belong to the
app, so there is no alpha-only description and no way to aim a listing change
at testers. What limits who sees it is whether a production release exists.
Say that out loud when someone authorises a listing change "for internal
testing only" -- the answer is usually that it is fine because nothing is
public yet, which is a different reason from the one they gave.

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

**Moving a build to another track is not another upload.** A bundle belongs to
the app, not to a track, and Play refuses a versionCode it has already
accepted -- so re-running `--aab` against alpha after internal fails. Assign
the existing code instead:

```bash
python3 <skill>/scripts/play_publish.py --version-code 1300 --track alpha --status completed
python3 <skill>/scripts/play_publish.py --version-code 1300 --track production   # draft by default
```

`--status completed` supersedes whatever is live on that track. The default
`draft` is added *beside* a live release rather than replacing it, so assigning
a draft can never silently un-publish a track. Testers then get the same bytes
that were tested, which is the point of promoting rather than rebuilding.

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

## Store listing images

`--screenshots <set>` replaces one listing image slot with what is in the
repo. Play has no partial update for a slot, so it deletes and re-uploads the
whole thing; that is also what makes it predictable, since the console ends up
showing exactly what the repo holds, in filename order.

```
python3 play_publish.py --screenshots tv        # or phone, tablet7, tablet10,
python3 play_publish.py --screenshots tvbanner  #    tvbanner, feature, icon
python3 play_publish.py --screenshots tv --language en-US
```

Sets resolve as repo-relative globs (`store/screenshots/tv/*.png`,
`store/feature-*.png`, ...), so the layout under `store/` is the only
convention a project has to follow. Counts are checked locally: Play takes 2-8
screenshots per set and exactly one of each graphic, and failing here names the
directory instead of returning a bare 400.

The language is resolved rather than assumed. With one listing there is nothing
to choose; with several, `--language` is required. Do not assume `en-US` -- an
app created in a UK account defaults to `en-GB`, and the wrong guess writes
images into a listing nobody sees.

**Shipping to Android TV needs two assets, not one.** `tvScreenshots` (16:9,
1280x720 or better) *and* `tvBanner` (1280x720). Play will not let the TV form
factor be opted into until both are uploaded, which makes this a gate on
shipping to television rather than listing polish. The store TV banner is a
different asset from the launcher banner baked into the APK at
`drawable-xhdpi/tv_banner.png` (320x180) -- render both from one source at
different scales so they cannot drift.

## Store listing copy

`--listing` pushes the listing text from the repo; `--listing-dry-run` shows
what that would change and sends nothing.

```
python3 play_publish.py --listing-dry-run    # diff live against the repo
python3 play_publish.py --listing            # push it
```

Copy that only reaches the store by being pasted into a console is not source
control, it is a second copy, and within a week nobody can say which is
current. The console is the one customers see. Both games that adopted the
listing convention found real drift the first time they diffed: in one case a
short description the repo had replaced long before, still live, plus two
claims the repo had already corrected as false.

**Play takes three fields, and the convention's directory holds eight.** The
gap is not an oversight:

- `edits.listings` takes **title, shortDescription, fullDescription**.
- **Release notes are not listing fields.** `store/listing/whats-new.txt`
  belongs to a track release, so `--aab` and `--version-code` attach it to the
  release they create and `--listing` leaves it alone. A listing write that
  carried it would clear the notes on whatever is live. Notes are per-language:
  an app with several listings gets a warning and no notes rather than a failed
  upload, since that is the wrong thing to lose a build over.
- **The privacy policy URL is an App content declaration**, entered in console.
- **Name, subtitle and keywords are Apple's**, with no Play equivalent.

Two things the API will let you get wrong:

- **PATCH the three fields; never PUT the resource.** The Listing resource also
  carries `video`, and a whole-resource write that omits it clears the video on
  a listing that had one. The write succeeds; the field is simply gone.
- **Validate before opening an edit.** An over-length field comes back as a 400
  naming neither the field nor the reason, with an edit already open. The push
  runs the repo's own listing validator first and refuses to send -- an npm
  `check:listing` script or a `check-listing` make target, whichever the repo
  has. A repo with neither is refused rather than pushed blind.

Keep this on its own command, away from anything that ships a build. Copy and
binaries move on different schedules, and fixing a typo in a description should
not need a new version code.

The field files and their rules are the `store-listing` convention's business,
not this skill's: see gelopard/playbook.

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
