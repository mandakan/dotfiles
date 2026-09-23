---
name: app-store-publish
description: Publish iOS builds to App Store Connect and TestFlight from any repo — provision signing through the API, archive, validate and upload an .ipa, then hand the processed build to internal testers — plus first-time setup of the API key, the bundle id and the app record. Use this whenever the user mentions TestFlight, App Store Connect, shipping or uploading an iOS build, internal or external testers, an .ipa or xcarchive, provisioning profiles, code signing identities, `xcrun altool`, App Store review, an Apple Developer team, or errors like "No profiles for ... were found", "Tester(s) cannot be assigned" or `errSecInternalComponent` — even when they don't name TestFlight, e.g. "get this build on my phone" or "why won't Xcode sign this".
---

# Publishing to App Store Connect

Getting an iOS build to testers is a handful of API calls wrapped in Apple
behaviours that fail with messages naming something other than their cause.
This skill carries those rules and a dependency-free client so you don't
rediscover them per repo.

`scripts/asc_publish.py` is the canonical copy. Run it directly for a one-off,
or install it into a repo when CI or the user needs to publish without you.

## The rules that actually bite

Read these first. Most iOS publishing failures are one of them, and every error
message points somewhere unhelpful.

**Sign manually. Automatic signing cannot work.** Archiving with automatic
signing makes Xcode ask Apple for a *development* provisioning profile, and
Apple will not issue one to a team with no registered devices — which is every
team that has only ever shipped through TestFlight. The archive fails with:

```
error: No Accounts: Add a new account in Accounts settings.
error: No profiles for 'com.example.app' were found: Xcode couldn't find any
       iOS App Development provisioning profiles matching ...
```

Both lines are misdirection. There is nothing wrong with the account and
nothing missing about the app. `--sign` provisions an **App Store** profile
through the API; sign manually against it.

**`CODE_SIGN_*` on the xcodebuild command line hits every target**, Swift
package ones included, which then fail with "does not support provisioning
profiles". Manual signing belongs in the app target's Release configuration. A
Godot-generated project has exactly one target, so the command line is safe
there and nowhere else.

**A generated Xcode project regenerates.** Godot rewrites the project and its
`export_options.plist` on every export, with automatic signing and an empty
profile, so that plist is never the one to export with. `--export-options`
writes the one to use.

**The distribution certificate is issued to the team, not the app**, and Apple
caps how many a team may hold. Every app on a team shares one, so a second app
needs no new certificate — only a profile. The script reuses any certificate
whose private key is already in the shared keychain and only creates one when
there is none.

**The signing keychain cannot be the login keychain.** A key imported there has
no partition list, and macOS then refuses to hand it to `codesign` without a
GUI prompt: the build fails with `errSecInternalComponent`, which names nothing
about keychains. Setting a partition list needs that keychain's password, and
the login password is not a build tool's to have. The script creates one at
`~/.appstoreconnect/signing/` with a password it generated, so this stays
headless.

**There is no `POST /v1/apps`.** Bundle ids, certificates, profiles, beta
groups and testers all have endpoints. The app record does not — it is created
in App Store Connect and nowhere else. So a first release has exactly one step
a person must do in a browser. `--dry-run` says whether it is done.

**Upload and distribution are two acts.** Apple processes the binary for a few
minutes after upload and it cannot be distributed until that finishes. Validate
before uploading, too: it catches the rejections that otherwise arrive by email
long after the upload appeared to succeed.

**A build number is spent per release.** App Store Connect rejects a second
upload carrying one it has already seen, so re-uploading needs a version bump
rather than a rebuild. Same rule as Play's versionCode.

## Publishing

```bash
python3 <skill>/scripts/asc_publish.py --check                 # config, offline
python3 <skill>/scripts/asc_publish.py --dry-run               # what exists now
python3 <skill>/scripts/asc_publish.py --register-bundle-id    # one-time
python3 <skill>/scripts/asc_publish.py --sign \
        --export-options build/ExportOptions.plist             # cert + profile
# ... archive and export with xcodebuild, then:
python3 <skill>/scripts/asc_publish.py --upload path/to.ipa
python3 <skill>/scripts/asc_publish.py --add-tester me@example.com
python3 <skill>/scripts/asc_publish.py --testflight            # once processed
python3 <skill>/scripts/asc_publish.py --listing               # copy + screenshots
python3 <skill>/scripts/asc_publish.py --declare               # rating, price, availability
```

`--listing` makes the repo the App Store listing: `store/listing/` (name,
subtitle, privacy policy URL, description, keywords, and what's-new once a
version has shipped) and every `store/screenshots/<slot>/` directory it knows
(`iphone-6.9`/`iphone-6.7` -> `APP_IPHONE_67`, `ipad-13` ->
`APP_IPAD_PRO_3GEN_129`), replaced whole. Categories come from
`asc.properties` (`primaryCategory`, `primarySubcategoryOne`,
`primarySubcategoryTwo`, Apple's ids such as `GAMES` and `GAMES_SIMULATION`)
when set. It edits the version that is not yet on sale, creating one at the
repo's version when none exists, and never submits for review. Locale from
`--locale` or `asc.properties` `locale`, else `en-US`.

`--declare` sets what a first submission needs beyond the listing, from the
terminal and idempotently: the age rating with every category none and every
boolean false (4+, `kidsAgeBand` left null so the app never enters the Kids
Category, which is a one-way door), free pricing from the USA base
territory's zero price point, availability in every territory plus new ones,
content rights (`contentRights` in `asc.properties`, default
`DOES_NOT_USE_THIRD_PARTY_CONTENT`), the release type (`releaseType`, default
`MANUAL`), and the review contact when `reviewFirstName`, `reviewLastName`,
`reviewEmail` and `reviewPhone` (in `+<country code>` form, or Apple refuses
the create with a 409) are all set, with notes from
`store/review-notes.txt` if it exists (outside `store/listing/`, whose
validator allows only the listing fields). Existing price schedules and
availabilities are left alone. App Privacy has no API and stays a console
step. First run for tussock, 2026-09-23.

**A second locale reads the translation directory with fallback.** `--locale
sv` takes each field from `store/listing/sv-SE/` when that file exists there
(the directory is named after Play's language code so one translation feeds
both stores; `sv` is matched to `sv-SE` by language part) and from the root
otherwise. The fallback is deliberate and differs from the Play publisher:
Apple's per-locale set is larger and several fields (`ios-name.txt`,
`privacy-policy-url.txt`) are the same string in every language, so a
translation carries only what differs, typically `ios-subtitle.txt` and
`ios-keywords.txt` beside Play's three. Screenshots are the same set for
every locale.

`--register-bundle-id` is deliberately its own flag and not implied by
`--sign`: Apple will not delete a bundle id once an app record uses it, so it
is the only irreversible write here and it asks to be typed.

`--add-tester` is its own flag for a softer reason — Apple emails an
invitation. Do not run it to test the code path.

Verify by reading state back with `--dry-run` rather than trusting an exit
code. The upload printing success and the build being where you think are
different claims.

## Configuration

Bundle id, team id and profile name are **read from the project** rather than
configured, because the project file is what the build signs against and a
second copy is a second thing that can be wrong, silently. Two shapes are
recognised:

| Shape | Bundle id | Team id | Profile |
|---|---|---|---|
| Godot | `export_presets.cfg` `application/bundle_identifier` | same file, `application/app_store_team_id` | defaults to `<bundle id> App Store` |
| Xcode / Capacitor | `**/project.pbxproj` `PRODUCT_BUNDLE_IDENTIFIER` | `ios/*.xcconfig` or the project, `DEVELOPMENT_TEAM` | `PROVISIONING_PROFILE_SPECIFIER` |

`bundleId`, `teamId` and `profileName` in `asc.properties` override all of it,
which is the escape hatch for a shape not listed. The profile is reissued on
every `--sign`, so its name only has to be stable and unique within the team.

Only the credentials are configured, and only two of them:

| Setting | Environment | asc.properties |
|---|---|---|
| API key id | `ASC_KEY_ID` | `ascKeyId` |
| Issuer id | `ASC_ISSUER_ID` | `ascIssuerId` |

## Handling the key

The `.p8` grants publishing rights across the whole team — every app on it.
Treat it like an upload keystore:

- It lives at `~/.appstoreconnect/private_keys/AuthKey_<id>.p8`, which is where
  `xcrun altool` looks too, so one file serves both. Mode `600`.
- **Only the `.p8` is secret.** The key id and the issuer id are identifiers.
  They belong in a gitignored config file rather than being guarded like
  credentials, and the issuer id is not derivable from the key, so it has to
  come from App Store Connect → Users and Access → Integrations.
- A team API key covers every app on the team, so a second game reuses the
  first one's key rather than minting another.
- The in-app-purchase key (`SubscriptionKey_*`) takes the **same** issuer id,
  even though the console presents it on its own page as if it were separate.
- Never print its contents. Validating it means signing a token, not reading
  the key.

If a user offers it inline in chat rather than as a path, take the path.

## Naming an app

The App Store name is unique across the **entire store**, and the conflict set
includes names **reserved** by app records that were never published. No public
search can see those: querying the iTunes Search API for a name App Store
Connect has already rejected returns a clean result. Do not present that check
as evidence of availability — it is only evidence about published apps.

Short plain nouns are the likeliest to be gone. Prefer a name that keeps the
app's existing identity, because the store name is not the only place a title
appears: the Play listing has its own name field and the home-screen label
comes from the app itself, so an unrelated store name means people install
something that looks like a different app.

## Errors worth recognising

**`409: Tester(s) cannot be assigned`** is app visibility, not the tester.
Internal testers are App Store Connect users, and a user with `allAppsVisible`
false sees only the apps explicitly granted to them. Grant visibility, then the
identical call succeeds. Add to the `visibleApps` relationship rather than
PATCHing it, which replaces the list and drops the user's other apps. Two
things that look like the cause and are not: the email may already have
`betaTester` records from sibling apps, and linking an existing record instead
of creating one fails identically.

**Apple returns 5xx** on calls that worked minutes earlier. That matters more
than a flake because reissuing a profile deletes the old one first, so a failed
create leaves the account with no profile and the next archive fails somewhere
less obvious. The script retries 5xx with a fresh token per attempt.

**`ITSAppUsesNonExemptEncryption`** belongs in the Info.plist, or every
TestFlight build stops after processing waiting for the export compliance
question. `--testflight` answers it through the API when it was not declared,
which is the same answer.

**`MinimumOSVersion` below 15.0** warns on upload today and is rejected from
Spring 2027.

## Screenshot slots

- 6.9" screenshots (1320x2868) go in the **`APP_IPHONE_67`** slot. Apple never
  added an `APP_IPHONE_69` and the API rejects it.
- A universal app needs a **13-inch iPad set (2064x2752)** as well as an iPhone
  one, for every listing update.
- Apple's sizes are not Play's, so a repo that has only shipped to Play is
  missing both Apple slots rather than just the iPad one.

## Installing into a repo

Copy the script in rather than running it from here:

1. `scripts/asc_publish.py` → the repo's tooling directory (`tools/`,
   `scripts/`, whatever it already uses — match it, don't impose).
2. `assets/asc.properties.example` → repo root.
3. Add `asc.properties` to `.gitignore`, and confirm with
   `git check-ignore -v asc.properties` rather than assuming. Check `.env` too
   while you are there; a repo whose other credential files are covered can
   still have an uncovered one.
4. Add targets matching the repo's build system. `assets/make-targets.mk` has
   Make ones.
5. Document it where the repo documents its other build steps, and name this
   skill as canonical so a later fix here can be re-synced rather than
   diverging:

```bash
md5 -q tools/asc_publish.py ~/.claude/skills/app-store-publish/scripts/asc_publish.py
```

Two equal hashes means in sync. Do this before trusting a vendored copy, not
only when something looks wrong — a repo that is behind still works for
everything it already knew how to do.

**Re-sync by overwriting, then diff to confirm the repo had nothing of its
own.** If the diff shows lines only the repo has, that is a fix which belongs
here first.

## Where the rules live

For gelopard repos, the rules above are the implementation of
`conventions/app-store-publish.md` in gelopard/playbook. When one turns out to
be wrong, fix it there as well as here: this skill holds the code, that page
holds the agreement.
