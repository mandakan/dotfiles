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
    python3 play_publish.py --promote --track internal
"""

import argparse
import base64
import json
import os
import pathlib
import re
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

PROPS_NAME = "play.properties"


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
        # The three failures worth naming, because the raw text for each sends
        # people looking in the wrong place.
        if exc.code == 401:
            die("Play rejected the credentials (401)",
                "the service account is not linked in Play Console under Users & permissions")
        if exc.code == 403:
            die("Play accepted the credentials but refused the action (403)",
                "grant the service account release permission on this app in Play Console")
        if exc.code == 404:
            die("Play does not recognise this app (404)",
                "check the package name, and confirm one bundle has been uploaded "
                "by hand -- the API cannot create a listing")
        die(f"Play API {method} failed ({exc.code})", detail)


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

    call(
        token, "PUT",
        f"{API}/{package}/edits/{edit_id}/tracks/{track}",
        body=json.dumps(
            {"track": track,
             "releases": [{"versionCodes": [str(version_code)], "status": status}]}
        ).encode(),
    )
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
    parser.add_argument("--promote", action="store_true",
                        help="roll the track's draft out to testers, uploading nothing")
    parser.add_argument("--dry-run", action="store_true",
                        help="authenticate and list the tracks, change nothing")
    parser.add_argument("--check", action="store_true",
                        help="validate local config only, no network")
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

    token = access_token(key_path)

    if args.dry_run:
        return show_tracks(token, package)
    if args.promote:
        return promote(token, package, args.track)

    if not args.aab:
        die("no bundle given", "pass --aab path/to/app.aab, or --promote / --dry-run")
    aab = pathlib.Path(args.aab)
    if not aab.exists():
        die(f"no bundle at {aab}", "build it first")
    if aab.suffix != ".aab":
        die(f"{aab} is not an .aab",
            "Play accepts App Bundles, not APKs, for upload to a track")
    return upload(token, package, aab, args.track, args.status)


if __name__ == "__main__":
    sys.exit(main())
