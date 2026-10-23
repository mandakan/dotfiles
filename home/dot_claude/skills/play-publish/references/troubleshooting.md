# Troubleshooting

Play's status codes point somewhere unhelpful more often than not. Match the
symptom, not the number.

## 404 on any call

**Almost always: the package has never had a manual upload.** The API cannot
create a listing and refuses a package it has not seen, and it says so with a
404 that reads like a wrong URL.

Also possible:
- The package name is wrong. It is the applicationId (`com.example.thing`),
  not the app's display name.
- The URL genuinely is wrong. Tracks live *inside an edit* — there is no
  top-level `/tracks` endpoint. If the response body is HTML rather than JSON,
  the URL is the problem: Play answers real API errors with JSON, so an HTML
  body means the request never reached the API surface.

## 401 Unauthorized

The credentials themselves were rejected. The service account is not linked in
Play Console under Users & permissions, or the key has been revoked or deleted
in Google Cloud. A malformed or truncated JSON key also lands here.

## 403 Forbidden

The credentials were accepted but the action was refused — an authorization
problem, not an authentication one. Either the account lacks release permission
on this app, or permissions were granted minutes ago and have not propagated.
Wait a few minutes before concluding it is misconfigured.

If the user is trying to reach production with an account granted only
"Release to testing tracks", this is also a 403 — and it is the grant working
as intended.

## "Version code N has already been used"

versionCode is unique per package forever, across every track. There is no way
to free one up; you must build a higher number.

Read the used codes first:

```bash
python3 play_publish.py --dry-run
```

Note that a code can be consumed by a release that was never rolled out — a
draft still burns it. The number to beat is the highest that appears anywhere,
not the highest that is live.

Bumping it is the project's concern: a version file, a Gradle property,
`export_presets.cfg`, whatever the build reads. Change it there and rebuild;
never hand-edit the bundle.

## "APKs are not allowed" / bundle rejected

Play takes App Bundles for track uploads, not APKs. If the build produces both,
upload the `.aab`.

A bundle can also be rejected for being **debuggable**. Debug exports set that
flag, and Play refuses them on every track including internal. The build must
be release-signed with a real upload key.

## The upload succeeded but testers see nothing

Check whether the release is a draft:

```bash
python3 play_publish.py --dry-run
```

A `draft` status means it is uploaded but not rolled out — that is the default,
by design. Promote it:

```bash
python3 play_publish.py --promote --track internal
```

If it shows `completed` and testers still see nothing: they may not be on the
tester list for that track, they may not have accepted the opt-in link, or Play
may still be processing — distribution is usually minutes but not instant.

## "no draft release on '<track>'"

`--promote` found nothing to roll out. Either the upload went straight to
`completed`, or it went to a different track than the one being promoted. Check
with `--dry-run`; the track name is easy to get wrong when several are in play.

## A run failed part-way

Nothing was published. Edits are transactional — the track only changes when
the commit succeeds, so a failure before that leaves it exactly as it was.
Fix the cause and rerun.

The one thing a failed run may leave behind is an abandoned edit, which is
harmless: edits expire on their own and do not block later ones.

## `cryptography` not installed

The only dependency. It is present in most Python environments; where it is
not:

```bash
python3 -m pip install --user cryptography
```

Everything else uses the standard library, deliberately, so that publishing
does not drag a dependency tree into a repo that has none.
