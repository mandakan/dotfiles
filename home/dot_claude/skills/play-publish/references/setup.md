# One-time setup

These steps are the user's to perform — they need Play Console and Google Cloud
access, and you cannot do them on their behalf. Walk them through it and then
verify with `--dry-run`, which is the first point where you can confirm the
whole chain actually works.

## 1. The app must exist, with one manual upload

The Publishing API cannot create a listing, and it rejects a package it has
never seen. So for a brand-new app:

1. Play Console → **Create app**. The package name (applicationId) is fixed
   forever at this point.
2. Complete whatever Play blocks release on — content rating, data safety,
   target audience, privacy policy. Internal testing needs less than production
   but is not exempt.
3. Build a **release-signed .aab** and upload it manually to a track.

Only after that will the API accept anything for this package. If the user is
automating an app that already ships, this is already done.

## 2. Link Play Console to a Google Cloud project

Play Console → **Setup → API access**. Link an existing Cloud project or create
one. This is what makes Play willing to trust service accounts from that
project.

## 3. Create the service account and key

In Google Cloud, for the linked project:

1. IAM & Admin → Service Accounts → **Create service account**. The name is
   arbitrary; something like `play-publisher` is clearer than the default.
2. Create a **JSON key** for it and download it.

No Cloud IAM roles are needed. The permission that matters is granted on the
Play side, in the next step — a service account with no Cloud roles at all
publishes fine.

## 4. Grant it release permission in Play Console

Play Console → **Users & permissions → Invite new users**. Use the service
account's email (`...@<project>.iam.gserviceaccount.com`).

Grant, scoped to the app rather than the whole account where possible:

- **Release to testing tracks** — enough for internal/alpha/beta.
- **Release to production** — only if the user intends to publish publicly.
  Don't grant it by default; the narrower grant limits the blast radius of a
  leaked key.

Permissions take a few minutes to propagate. A 403 immediately after granting
usually means "wait", not "wrong".

## 5. Store the key and point at it

```bash
mv ~/Downloads/<downloaded>.json ~/keystores/<account>-play-api.json
chmod 600 ~/keystores/<account>-play-api.json
```

Name it after the **Play account**, not the app. One service account can be
granted permission on every app in the account, and usually is — so a key named
after whichever app happened to need it first invites the assumption that each
app has its own. It doesn't, and that matters: whoever holds this file can
publish any app on the account.

Then either export `PLAY_SERVICE_ACCOUNT` / `PLAY_PACKAGE`, or create
`play.properties` from the example asset. Confirm it is ignored:

```bash
git check-ignore -v play.properties
```

Do this before the key ever sits in a working tree — a gitignore added after an
accidental commit does nothing for the history.

## 6. Verify

```bash
python3 play_publish.py --check      # local config only
python3 play_publish.py --dry-run    # authenticates, lists tracks
```

`--dry-run` succeeding proves the whole chain: key valid, service account
linked, permission granted, package recognised. It also shows the versionCodes
already used, which tells you what the next build has to exceed.

## For CI

Store the key as a secret (base64-encoded, since it is multi-line JSON), decode
it to a temp file at job start, export `PLAY_SERVICE_ACCOUNT` at that path, and
delete it at the end. Prefer a separate service account for CI with only
testing-track permission, so a compromised runner cannot reach production.

Be honest with the user about the tradeoff: this puts a publishing credential
on infrastructure they don't control. For a solo project, publishing from a
local machine is a defensible choice.
