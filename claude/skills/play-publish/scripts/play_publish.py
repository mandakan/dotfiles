#!/usr/bin/env python3
"""Publish an Android App Bundle to a Google Play track, and roll it out.

Build-system agnostic: it never inspects your project. The package name comes
from config and the versionCode comes back from Play in the upload response, so
this works the same for Gradle, Flutter, Unity, Godot or a bundle someone
handed you.

Credentials resolve environment-first, then play.properties. The service
account key grants publishing rights to the whole Play account, so it lives
OUTSIDE the repo and that file only points at it. Nothing here ever prints it.

No third-party packages. The service-account OAuth flow is a signed JWT
exchanged for an access token, which `cryptography` can do -- that avoids
making every repo that publishes carry google-api-python-client.

The Play Edits API is transactional: open an edit, act inside it, then commit.
Nothing is visible until the commit succeeds, so a failure part-way through
leaves the track exactly as it was.

Usage:
    python3 play_publish.py --check
    python3 play_publish.py --dry-run
    python3 play_publish.py --aab build/app.aab --track internal
    python3 play_publish.py --version-code 1300 --track alpha --status completed
    python3 play_publish.py --promote --track internal
    python3 play_publish.py --screenshots tv
    python3 play_publish.py --listing-dry-run
    python3 play_publish.py --listing
"""

import argparse
import base64
import difflib
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://androidpublisher.googleapis.com/androidpublisher/v3/applications"
UPLOAD = "https://androidpublisher.googleapis.com/upload/androidpublisher/v3/applications"
SCOPE = "https://www.googleapis.com/auth/androidpublisher"

# Play rejects anything else, and a typo would otherwise surface as an opaque
# 400 after the bundle has already been uploaded.
TRACKS = ("internal", "alpha", "beta", "production")

# Play listing image slots, and where this repo keeps each set. The TV slot is
# the reason this mode exists: Play will not let the Android TV form factor be
# opted into at all until 16:9 screenshots are uploaded, which makes it a gate
# on shipping to television rather than a listing nicety.
# (Play image type, repo-relative glob, how many images Play accepts). Globs
# rather than fixed paths so one copy of this script serves every game: the
# directory layout under store/ is a shared convention, the exact filenames are
# not. The graphic slots hold exactly one image; the screenshot sets take 2-8.
SCREENSHOT_SETS = {
    "tv": ("tvScreenshots", "store/screenshots/tv/*.png", 2, 8),
    "phone": ("phoneScreenshots", "store/screenshots/*.png", 2, 8),
    "tablet7": ("sevenInchScreenshots", "store/screenshots/tablet-7/*.png", 2, 8),
    "tablet10": ("tenInchScreenshots", "store/screenshots/tablet-10/*.png", 2, 8),
    # Play asks for a TV banner before the TV form factor can be opted into, and
    # it is a different asset from the launcher banner baked into the APK.
    "tvbanner": ("tvBanner", "store/tv-banner/*.png", 1, 1),
    "feature": ("featureGraphic", "store/feature-*.png", 1, 1),
    "icon": ("icon", "store/icon-*.png", 1, 1),
}

# The listing fields Play's API will actually take, and where the convention
# keeps each. This is NARROWER than the eight files store/listing/ holds, and
# the gap is the point:
#   - release notes belong to a track release, not a listing, so they attach at
#     an upload or a version-code assignment. A listing write that included them
#     would clear the notes on the live release.
#   - the privacy policy URL is an App content declaration, entered in console.
#   - app-store name, subtitle and keywords are Apple's, with no Play equivalent.
LISTING_FIELDS = {
    "title": "store/listing/app-name.txt",
    "shortDescription": "store/listing/short-description.txt",
    "fullDescription": "store/listing/full-description.txt",
}

# The ninth thing store/listing/ holds, and the one that is not a listing field.
# Release notes belong to a track release, so they are attached where a release
# is made -- an upload or a version-code assignment -- and never by a listing
# write. Without this the file is an orphan: written, validated by the repo's
# listing validator, documented as pushed, and pushed by nothing, so whatever
# was last typed into the console ships as the release notes instead.
RELEASE_NOTES_FILE = "store/listing/whats-new.txt"
RELEASE_NOTES_LIMIT = 500

# How each game runs its own listing validator. The convention puts a validator
# in every repo but lets each write it in the language the repo already speaks,
# so this looks for the target rather than the script.
VALIDATORS = (
    (["npm", "run", "--silent", "check:listing"], "package.json", '"check:listing"'),
    (["make", "check-listing"], "Makefile", "check-listing:"),
)

PROPS_NAME = "play.properties"

# Screenshot sets are repo-relative, so resolve them from this file rather than
# the working directory: every other path here comes from a flag or walks up
# from cwd, and a screenshot upload should not depend on where it was invoked.
ROOT = pathlib.Path(__file__).resolve().parent.parent


def find_props() -> pathlib.Path | None:
    """Look for play.properties from the working directory upward.

    Walking up means the script works whether it is invoked from the repo root
    or a subdirectory, without the caller having to think about it.
    """
    here = pathlib.Path.cwd().resolve()
    for directory in (here, *here.parents):
        candidate = directory / PROPS_NAME
        if candidate.exists():
            return candidate
    return None


def read_props() -> dict[str, str]:
    path = find_props()
    if path is None:
        return {}
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if m:
            out[m[1]] = m[2].strip()
    return out


def resolve(env_name: str, prop_name: str, props: dict[str, str]) -> str | None:
    value = os.environ.get(env_name) or props.get(prop_name)
    if not value:
        return None
    # A leading ~ is not expanded by the shell inside a properties file, and a
    # properties file is exactly where someone writes one.
    if value.startswith("~"):
        value = str(pathlib.Path.home()) + value[1:]
    return value


def die(message: str, hint: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    print(f"       {hint}", file=sys.stderr)
    sys.exit(1)


def access_token(key_path: str) -> str:
    """Exchange a service-account JWT for an OAuth access token."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    try:
        key = json.loads(pathlib.Path(key_path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        die(f"cannot read service account key at {key_path}", str(exc))

    for field in ("client_email", "private_key"):
        if field not in key:
            die(
                f"service account key is missing '{field}'",
                "download the JSON key from Google Cloud, not from Play Console",
            )

    def b64(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    now = int(time.time())
    header = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = b64(
        json.dumps(
            {
                "iss": key["client_email"],
                "scope": SCOPE,
                "aud": TOKEN_URL,
                "iat": now,
                "exp": now + 3600,
            }
        ).encode()
    )
    signing_input = header + b"." + claims
    private = serialization.load_pem_private_key(key["private_key"].encode(), password=None)
    signature = private.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    assertion = (signing_input + b"." + b64(signature)).decode()

    body = urllib.parse.urlencode(
        {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}
    ).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=body)) as resp:
            return json.load(resp)["access_token"]
    except urllib.error.HTTPError as exc:
        die(
            f"Google rejected the service account ({exc.code})",
            "check the key is current and the account still exists in Google Cloud",
        )


def call(token: str, method: str, url: str, body: bytes | None = None,
         content_type: str = "application/json") -> dict:
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            detail = json.loads(raw)["error"]["message"]
        except Exception:
            # An HTML body means the URL was wrong, not the request -- Play
            # answers real API errors with JSON.
            detail = ("unexpected non-JSON response; check the request URL"
                      if raw.lstrip().startswith("<") else raw[:400])
        # ALWAYS lead with what Play actually said. An earlier version of this
        # replaced the server's message with a guess for 403 and 404, which sent
        # two separate investigations to the wrong place: a 403 reading "Please
        # migrate to the new publishing API" is a retired endpoint, not a missing
        # permission, and an HTML 404 is a wrong URL or verb, not an unknown app.
        # The guesses are still worth printing -- underneath the evidence, not
        # instead of it.
        if exc.code == 401:
            die("Play rejected the credentials (401)",
                "the service account is not linked in Play Console under Users & permissions")
        if exc.code == 403:
            die(f"Play refused the action (403): {detail}",
                "if that message is about permissions, grant the service account release "
                "permission on this app in Play Console; if it names the API itself, the "
                "endpoint is retired and the call needs updating")
        if exc.code == 404:
            die(f"Play returned 404: {detail}",
                "a JSON message means the package is unknown -- check the name, and confirm "
                "one bundle was uploaded by hand, since the API cannot create a listing; the "
                "non-JSON note above instead means this URL or HTTP verb has no route")
        die(f"Play API {method} failed ({exc.code})", detail)


def upload_screenshots(token: str, package: str, which: str, language: str) -> int:
    """Replace one listing image slot with the committed PNGs for it.

    Play's images collection has no partial update: the slot is deleted and
    re-uploaded whole, which is also what makes the result predictable -- the
    console ends up showing exactly what is in the repo, in filename order.
    Like every other mode here it happens inside one edit, so a failure part-way
    leaves the listing untouched.

    What this CANNOT do is change the listing's default language. The Publishing
    API accepts the edits.details write and then refuses the commit with a 403;
    the default language is a Play Console setting only. Adding a second language
    and filling it works fine, which is as far as automation gets.
    """
    image_type, pattern, low, high = SCREENSHOT_SETS[which]
    shots = sorted(ROOT.glob(pattern))
    if not shots:
        die(f"nothing matches {pattern}",
            "generate the assets first, then re-run")
    # Play's own bounds. Failing here beats a 400 that names no file.
    if not low <= len(shots) <= high:
        die(f"{len(shots)} image(s) matching {pattern}; Play accepts "
            + (f"exactly {low}" if low == high else f"{low} to {high}"),
            "fix it in the generator, not by hand in this directory")

    edit = call(token, "POST", f"{API}/{package}/edits")
    edit_id = edit["id"]

    language = resolve_language(token, package, edit_id, language)

    existing = call(token, "GET",
                    f"{API}/{package}/edits/{edit_id}/listings/{language}/{image_type}")
    had = len(existing.get("images", []))
    print(f"==> {image_type} ({language}): replacing {had} image(s) with {len(shots)} from {pattern}")
    if had:
        call(token, "DELETE",
             f"{API}/{package}/edits/{edit_id}/listings/{language}/{image_type}")

    for shot in shots:
        call(token, "POST",
             f"{UPLOAD}/{package}/edits/{edit_id}/listings/{language}/{image_type}?uploadType=media",
             body=shot.read_bytes(), content_type="image/png")
        print(f"    {shot.name} ({shot.stat().st_size // 1024} KiB)")

    call(token, "POST", f"{API}/{package}/edits/{edit_id}:commit")
    print(f"==> committed {len(shots)} {image_type} to the '{language}' listing")
    return 0


def resolve_language(token: str, package: str, edit_id: str, language: str | None) -> str:
    """Which listing language to act on, asked rather than assumed.

    It is per-app and easy to guess wrong -- an app created in a UK account
    defaults to en-GB -- so with a single listing there is nothing to choose
    between, and with several the caller has to say. Discards the open edit
    before dying: an abandoned edit is harmless but a confusing thing to leave.
    """
    listings = call(token, "GET", f"{API}/{package}/edits/{edit_id}/listings")
    languages = [l["language"] for l in listings.get("listings", [])]
    if language is None:
        if len(languages) == 1:
            return languages[0]
        call(token, "DELETE", f"{API}/{package}/edits/{edit_id}")
        die(f"{len(languages)} listing languages on this app",
            f"pass --language, one of: {', '.join(languages) or 'none'}")
    if language not in languages:
        call(token, "DELETE", f"{API}/{package}/edits/{edit_id}")
        die(f"no '{language}' listing on this app",
            f"existing listings: {', '.join(languages) or 'none'}; pass --language")
    return language


def read_listing_files() -> dict[str, str]:
    """The three pushable fields, read from the repo.

    One trailing newline is the file ending rather than listing content, which
    is the same rule the convention's validator counts by; anything more would
    arrive in the console as blank lines at the bottom of the description.
    """
    out = {}
    for field, rel in LISTING_FIELDS.items():
        path = ROOT / rel
        if not path.exists():
            die(f"{rel} not found", "the listing convention keeps one field per file under store/listing/")
        out[field] = path.read_text(encoding="utf-8").rstrip("\n")
    return out


def release_notes(token: str, package: str, edit_id: str) -> list[dict[str, str]] | None:
    """whats-new.txt shaped as a Play releaseNotes list, or None to send none.

    Reuses the caller's open edit rather than opening its own: this runs inside
    an upload that has already put a bundle in that edit, and a second edit
    would be a second lock on the same app.

    Degrades rather than fails. Notes are per-language and Play rejects a
    language the app has no listing for, so an app with several listings and no
    way to choose gets a warning and no notes -- failing somebody's upload over
    a nicety on the release would be the wrong trade. Over-length is different:
    that is a fixable mistake in a file, and silently dropping it would teach
    nobody.
    """
    path = ROOT / RELEASE_NOTES_FILE
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").rstrip("\n")
    if not text:
        return None
    if len(text) > RELEASE_NOTES_LIMIT:
        die(f"{RELEASE_NOTES_FILE} is {len(text)} characters, Play allows {RELEASE_NOTES_LIMIT}",
            "shorten it and re-run the repo's listing validator")

    listings = call(token, "GET", f"{API}/{package}/edits/{edit_id}/listings")
    languages = [l["language"] for l in listings.get("listings", [])]
    if len(languages) != 1:
        print(f"    note: {len(languages)} listing languages, so no release notes were "
              f"attached; set them in the console", file=sys.stderr)
        return None

    print(f"==> release notes from {RELEASE_NOTES_FILE} "
          f"({len(text)} chars, {languages[0]})")
    return [{"language": languages[0], "text": text}]


def preflight_validate() -> None:
    """Run the repo's own listing validator, and refuse to send if it fails.

    An over-length field comes back from Play as a 400 that names neither the
    field nor the reason, with an edit already open. Catching it here costs a
    few milliseconds and turns that into a readable failure before anything is
    opened, which is why this runs BEFORE the edit rather than inside it.
    """
    for command, marker_file, marker_text in VALIDATORS:
        marker = ROOT / marker_file
        if marker.exists() and marker_text in marker.read_text(encoding="utf-8", errors="replace"):
            print(f"==> pre-flight: {' '.join(command)}")
            result = subprocess.run(command, cwd=ROOT)
            if result.returncode != 0:
                die("the listing validator failed", "fix the copy it named; nothing was sent")
            return
    die("no listing validator in this repo",
        "the convention wants one per repo -- an npm 'check:listing' script or a "
        "'check-listing' make target -- and this refuses to push copy nothing has checked")


def diff_listing(current: dict, wanted: dict[str, str]) -> list[str]:
    """Print what would change, field by field. Returns the fields that differ."""
    changed = []
    for field, new_text in wanted.items():
        old_text = current.get(field) or ""
        if old_text == new_text:
            print(f"    = {field} ({len(new_text)} chars, unchanged)")
            continue
        changed.append(field)
        print(f"    ~ {field} ({len(old_text)} -> {len(new_text)} chars)")
        for line in difflib.unified_diff(
                old_text.splitlines(), new_text.splitlines(),
                fromfile="live", tofile="repo", lineterm="", n=0):
            if line.startswith(("---", "+++")):
                continue
            print(f"        {line}")
    return changed


def push_listing(token: str, package: str, language: str | None, dry_run: bool) -> int:
    """Push title, short and full description from the repo to the listing.

    Files that only ever reach the store by being pasted into a console are not
    source control, they are a second copy, and within a week nobody can say
    which one is current. The console is the one customers see.

    The write is a PATCH of exactly the three fields. The Listing resource also
    carries `video`, and a whole-resource PUT that omitted it would clear it.
    """
    wanted = read_listing_files()
    if not dry_run:
        preflight_validate()

    edit = call(token, "POST", f"{API}/{package}/edits")
    edit_id = edit["id"]
    language = resolve_language(token, package, edit_id, language)
    current = call(token, "GET", f"{API}/{package}/edits/{edit_id}/listings/{language}")

    print(f"==> listing '{language}' on {package}")
    changed = diff_listing(current, wanted)

    if dry_run:
        call(token, "DELETE", f"{API}/{package}/edits/{edit_id}")
        print("edit discarded; nothing changed")
        return 0
    if not changed:
        call(token, "DELETE", f"{API}/{package}/edits/{edit_id}")
        print("==> already matches the repo; nothing to send")
        return 0

    call(token, "PATCH", f"{API}/{package}/edits/{edit_id}/listings/{language}",
         body=json.dumps(wanted).encode())
    call(token, "POST", f"{API}/{package}/edits/{edit_id}:commit")
    print(f"==> committed {', '.join(changed)} to the '{language}' listing")
    return 0


def show_tracks(token: str, package: str) -> int:
    """Read every track back. Opens an edit and discards it, changing nothing."""
    edit = call(token, "POST", f"{API}/{package}/edits")
    info = call(token, "GET", f"{API}/{package}/edits/{edit['id']}/tracks")
    call(token, "DELETE", f"{API}/{package}/edits/{edit['id']}")
    print(f"authenticated OK for {package}")
    for track in info.get("tracks", []):
        codes = [c for r in track.get("releases", []) for c in r.get("versionCodes", [])]
        statuses = [r.get("status") for r in track.get("releases", [])]
        print(f"  {track['track']:12} versionCodes={codes or '-'} status={statuses or '-'}")
    print("edit discarded; nothing changed")
    return 0


def upload(token: str, package: str, aab: pathlib.Path, track: str, status: str) -> int:
    print(f"==> uploading {aab} ({aab.stat().st_size // 1024} KiB) to '{track}'")
    edit = call(token, "POST", f"{API}/{package}/edits")
    edit_id = edit["id"]

    uploaded = call(
        token, "POST",
        f"{UPLOAD}/{package}/edits/{edit_id}/bundles?uploadType=media",
        body=aab.read_bytes(),
        content_type="application/octet-stream",
    )
    version_code = uploaded["versionCode"]
    print(f"==> uploaded versionCode {version_code}")

    release: dict[str, object] = {"versionCodes": [str(version_code)], "status": status}
    notes = release_notes(token, package, edit_id)
    if notes:
        release["releaseNotes"] = notes

    call(
        token, "PUT",
        f"{API}/{package}/edits/{edit_id}/tracks/{track}",
        body=json.dumps({"track": track, "releases": [release]}).encode(),
    )
    call(token, "POST", f"{API}/{package}/edits/{edit_id}:commit")
    print(f"==> committed to '{track}' as {status} (versionCode {version_code})")
    if status == "draft":
        print("    roll it out with: --promote")
    return 0


def assign(token: str, package: str, version_code: str, track: str, status: str) -> int:
    """Put an already-uploaded bundle on another track, uploading nothing.

    A bundle belongs to the app, not to a track, and Play never accepts the
    same versionCode twice -- so a build that is already on internal cannot be
    re-uploaded to reach alpha or production. Moving it is a track assignment,
    which is this. Same bytes the testers already have, which is the point:
    promoting across tracks should not rebuild.
    """
    edit = call(token, "POST", f"{API}/{package}/edits")
    edit_id = edit["id"]

    known = {
        str(b.get("versionCode"))
        for b in call(token, "GET", f"{API}/{package}/edits/{edit_id}/bundles").get("bundles", [])
    }
    if version_code not in known:
        call(token, "DELETE", f"{API}/{package}/edits/{edit_id}")
        die(f"versionCode {version_code} was never uploaded for {package}",
            "upload it first with --aab"
            + (f", or pick one of: {', '.join(sorted(known))}" if known else ""))

    # The whole tracks collection rather than one track: a track that has never
    # held a release answers 404 here, which call() reports as an unknown app.
    tracks = call(token, "GET", f"{API}/{package}/edits/{edit_id}/tracks").get("tracks", [])
    current = next((t for t in tracks if t.get("track") == track), {})
    live = [r for r in current.get("releases", []) if r.get("status") == "completed"]

    release: dict[str, object] = {"versionCodes": [version_code], "status": status}
    notes = release_notes(token, package, edit_id)
    if notes:
        release["releaseNotes"] = notes
    if status == "draft":
        # A draft may sit beside whatever is already live, and must: sending the
        # draft alone would drop the live release and un-publish the track.
        releases = live + [release]
    else:
        # One live release per track, so this supersedes what was there.
        releases = [release]
        superseded = [c for r in live for c in r.get("versionCodes", [])]
        if superseded:
            print(f"    superseding {', '.join(superseded)}")

    print(f"==> assigning versionCode {version_code} to '{track}' as {status}")
    call(token, "PUT", f"{API}/{package}/edits/{edit_id}/tracks/{track}",
         body=json.dumps({"track": track, "releases": releases}).encode())
    call(token, "POST", f"{API}/{package}/edits/{edit_id}:commit")
    print(f"==> committed to '{track}' as {status} (versionCode {version_code})")
    if status == "draft":
        print("    roll it out with: --promote")
    return 0


def promote(token: str, package: str, track: str) -> int:
    """Roll the track's draft release out to testers.

    Play allows one live release per track, so the promoted release is sent as
    the track's only release and supersedes whatever was live before. Sending
    it alongside the previous one is rejected.
    """
    edit = call(token, "POST", f"{API}/{package}/edits")
    edit_id = edit["id"]
    current = call(token, "GET", f"{API}/{package}/edits/{edit_id}/tracks/{track}")

    drafts = [r for r in current.get("releases", []) if r.get("status") == "draft"]
    if not drafts:
        call(token, "DELETE", f"{API}/{package}/edits/{edit_id}")
        die(f"no draft release on '{track}'", "nothing to roll out; upload one first")
    if len(drafts) > 1:
        call(token, "DELETE", f"{API}/{package}/edits/{edit_id}")
        die(f"{len(drafts)} draft releases on '{track}'",
            "ambiguous which to roll out; resolve it in Play Console")

    release = dict(drafts[0])
    release["status"] = "completed"
    superseded = [
        c for r in current.get("releases", []) if r.get("status") == "completed"
        for c in r.get("versionCodes", [])
    ]
    print(f"==> rolling out {release.get('name')} "
          f"(versionCode {', '.join(release.get('versionCodes', []))}) on '{track}'")
    if superseded:
        print(f"    superseding {', '.join(superseded)}")

    call(token, "PUT", f"{API}/{package}/edits/{edit_id}/tracks/{track}",
         body=json.dumps({"track": track, "releases": [release]}).encode())
    call(token, "POST", f"{API}/{package}/edits/{edit_id}:commit")
    print(f"==> live on '{track}'")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--aab", default=None, help="path to the .aab to upload")
    parser.add_argument("--track", default="internal", choices=TRACKS)
    parser.add_argument("--package", default=None,
                        help="applicationId; else PLAY_PACKAGE or play.properties")
    parser.add_argument("--status", default="draft", choices=("draft", "completed"),
                        help="draft leaves the release for a separate --promote")
    parser.add_argument("--version-code", default=None,
                        help="assign an already-uploaded bundle to --track, uploading nothing")
    parser.add_argument("--promote", action="store_true",
                        help="roll the track's draft out to testers, uploading nothing")
    parser.add_argument("--dry-run", action="store_true",
                        help="authenticate and list the tracks, change nothing")
    parser.add_argument("--check", action="store_true",
                        help="validate local config only, no network")
    parser.add_argument("--screenshots", default=None, choices=tuple(SCREENSHOT_SETS),
                        help="replace a listing image slot from store/, uploading no bundle")
    parser.add_argument("--listing", action="store_true",
                        help="push title and both descriptions from store/listing/, uploading no bundle")
    parser.add_argument("--listing-dry-run", action="store_true",
                        help="diff store/listing/ against the live listing and change nothing")
    parser.add_argument("--language", default=None,
                        help="listing language for --screenshots and --listing; default: the only one, if there is one")
    args = parser.parse_args()

    props = read_props()
    key_path = resolve("PLAY_SERVICE_ACCOUNT", "serviceAccountFile", props)
    package = args.package or resolve("PLAY_PACKAGE", "packageName", props)

    if not key_path:
        die("no Play service account configured",
            "create play.properties (see the skill's assets/play.properties.example) "
            "or set PLAY_SERVICE_ACCOUNT")
    if not pathlib.Path(key_path).exists():
        die(f"service account key not found at {key_path}",
            "check serviceAccountFile in play.properties")
    if not package:
        die("no package name",
            "set packageName in play.properties, PLAY_PACKAGE, or pass --package")

    if args.check:
        print(f"Play config OK: {package} via {key_path}", file=sys.stderr)
        return 0

    # Copy and binaries move on different schedules: a typo fixed in a
    # description should not need a new version code, so the listing push is its
    # own mode rather than a step inside an upload.

    token = access_token(key_path)

    if args.dry_run:
        return show_tracks(token, package)
    if args.listing or args.listing_dry_run:
        return push_listing(token, package, args.language, args.listing_dry_run)
    if args.screenshots:
        return upload_screenshots(token, package, args.screenshots, args.language)
    if args.promote:
        return promote(token, package, args.track)
    if args.version_code:
        return assign(token, package, args.version_code, args.track, args.status)

    if not args.aab:
        die("no bundle given",
            "pass --aab path/to/app.aab, or --version-code / --promote / --dry-run")
    aab = pathlib.Path(args.aab)
    if not aab.exists():
        die(f"no bundle at {aab}", "build it first")
    if aab.suffix != ".aab":
        die(f"{aab} is not an .aab",
            "Play accepts App Bundles, not APKs, for upload to a track")
    return upload(token, package, aab, args.track, args.status)


if __name__ == "__main__":
    sys.exit(main())
