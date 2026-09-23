#!/usr/bin/env python3
"""App Store Connect: provision signing, upload a build, hand it to testers.

The Play counterpart is tools/play_publish.py and this deliberately mirrors its
shape -- env-first config, --check offline, --dry-run reads without writing, and
every claim verified by reading state back rather than trusting an exit code.

This is the canonical copy. Repos vendor it into their own tooling directory
and name this skill as the source, the same arrangement play_publish.py has --
so fix a bug here and re-sync, rather than editing a vendored copy in place.
Two equal hashes means in sync:

    md5 -q tools/asc_publish.py \
           ~/.claude/skills/app-store-publish/scripts/asc_publish.py

The rules it encodes, as opposed to the code, live in gelopard/playbook's
conventions/app-store-publish.md. When one turns out to be wrong, fix it
there too.

Only the .p8 is secret. The key id and the issuer id are identifiers, and live
in asc.properties (gitignored) beside play.properties, not in the repo.

Why manual signing, when automatic is one flag: archiving with automatic
signing makes Xcode ask Apple for a *development* provisioning profile, and
Apple will not issue one to a team with no registered devices. A team that has
only ever shipped through TestFlight has none, so the first archive fails on an
error about devices that has nothing to do with what an App Store build needs.
Manual signing with an App Store profile skips the question entirely.
"""

import argparse
import base64
import hashlib
import json
import os
import pathlib
import plistlib
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
API = "https://api.appstoreconnect.apple.com"

# Shared with skugga and wake-alone on purpose. The distribution certificate is
# issued to the team rather than to an app, and Apple caps how many a team may
# hold, so a second keychain would mean burning a second certificate.
SIGNING_DIR = pathlib.Path.home() / ".appstoreconnect" / "signing"
KEYCHAIN = SIGNING_DIR / "skein-signing.keychain"
KEYCHAIN_PASSWORD_FILE = SIGNING_DIR / "keychain.password"
PRIVATE_KEYS = pathlib.Path.home() / ".appstoreconnect" / "private_keys"

# Xcode 16 moved its profile store; older Xcodes and altool still read the
# original path, so write both rather than guess at the running version.
PROFILE_DIRS = [
    pathlib.Path.home() / "Library" / "MobileDevice" / "Provisioning Profiles",
    pathlib.Path.home() / "Library" / "Developer" / "Xcode" / "UserData" / "Provisioning Profiles",
]

# /usr/bin/openssl explicitly: a Homebrew OpenSSL 3 earlier on PATH writes
# PKCS#12 archives that Keychain refuses to import.
OPENSSL = "/usr/bin/openssl"

GROUP_NAME = "Internal"

DIM, GREEN, RED, RESET = "\033[2m", "\033[32m", "\033[31m", "\033[0m"


def fail(message: str, hint: str = "") -> None:
    print(f"{RED}ERROR{RESET} {message}")
    if hint:
        print(f"      {DIM}{hint}{RESET}")
    raise SystemExit(1)


def step(message: str) -> None:
    print(f"{GREEN}ok{RESET}   {DIM}{message}{RESET}")


def sh(*command: str) -> str:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"{command[0]} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


# --- config ----------------------------------------------------------------

def properties() -> dict[str, str]:
    """Read asc.properties, walking up from the repo root like play.properties."""
    values: dict[str, str] = {}
    path = ROOT / "asc.properties"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip("\"'")
    return values


def _search(paths: list[pathlib.Path], pattern: str) -> str:
    """First capture of `pattern` across `paths`, or empty."""
    for path in paths:
        if not path.is_file():
            continue
        match = re.search(pattern, path.read_text(errors="ignore"), re.M)
        if match:
            return match.group(1).strip()
    return ""


def discover(root: pathlib.Path) -> dict[str, str]:
    """Bundle id, team id and profile name, from whatever the repo actually is.

    Read rather than configured, because the project file is what the build
    signs against: a second copy in a config file is a second thing that can be
    wrong, and it would be wrong silently. Two shapes are supported, which is
    every gelopard game:

      Godot      export_presets.cfg   application/bundle_identifier
                                      application/app_store_team_id
      Capacitor  **/project.pbxproj   PRODUCT_BUNDLE_IDENTIFIER
                 ios/*.xcconfig       DEVELOPMENT_TEAM

    A Capacitor project also names the profile it signs against, so that is
    read too; a generated Xcode project (Godot) names none, and the default
    below is used instead. Anything explicit in asc.properties wins over both,
    which is the escape hatch for a shape not listed here.
    """
    preset = root / "export_presets.cfg"
    # Only the game's own project: a build directory holds every Swift
    # package's project too (RevenueCat's ships several), and build/ sorts
    # before ios/, so an unfiltered glob answers with the wrong bundle id.
    skip = {"build", "node_modules", "DerivedData", "Pods", ".build", "SourcePackages"}
    pbxproj = sorted(p for p in root.glob("**/*.xcodeproj/project.pbxproj")
                     if not skip & set(p.relative_to(root).parts))
    xcconfig = sorted(root.glob("ios/*.xcconfig"))

    found = {
        "bundle_id": _search([preset], r'^application/bundle_identifier="([^"]+)"$')
        or _search(pbxproj, r"^\s*PRODUCT_BUNDLE_IDENTIFIER\s*=\s*\"?([^\";\n]+?)\"?\s*;"),
        "team_id": _search([preset], r'^application/app_store_team_id="([^"]+)"$')
        or _search(xcconfig + pbxproj, r"^\s*DEVELOPMENT_TEAM\s*=\s*\"?([^\";\n]+?)\"?\s*;?\s*$"),
        "profile_name": _search(
            pbxproj, r"^\s*PROVISIONING_PROFILE_SPECIFIER\s*=\s*\"([^\"]+)\"\s*;"),
    }
    return found


class Config:
    def __init__(self) -> None:
        props = properties()
        self.key_id = os.environ.get("ASC_KEY_ID") or props.get("ascKeyId", "")
        self.issuer = os.environ.get("ASC_ISSUER_ID") or props.get("ascIssuerId", "")
        self.key_path = PRIVATE_KEYS / f"AuthKey_{self.key_id}.p8" if self.key_id else None
        found = discover(ROOT)
        self.bundle_id = props.get("bundleId") or found["bundle_id"]
        self.team_id = props.get("teamId") or found["team_id"]
        # Reissued on every --sign, so the name only has to be stable and
        # unique within the team, not memorable.
        self.profile_name = (props.get("profileName") or found["profile_name"]
                             or f"{self.bundle_id} App Store")

    @property
    def ok(self) -> bool:
        return bool(self.key_id and self.issuer and self.key_path and self.key_path.exists())

    def problems(self) -> list[str]:
        out = []
        if not self.key_id:
            out.append("no ASC_KEY_ID (env) or ascKeyId (asc.properties)")
        if not self.issuer:
            out.append("no ASC_ISSUER_ID (env) or ascIssuerId (asc.properties)")
        if self.key_id and self.key_path and not self.key_path.exists():
            out.append(f"no private key at {self.key_path}")
        if not self.bundle_id:
            out.append("no bundle id: none in export_presets.cfg or an Xcode "
                       "project, and no bundleId in asc.properties")
        if not self.team_id or self.team_id == "XXXXXXXXXX":
            out.append("no real team id: none in export_presets.cfg, ios/*.xcconfig "
                       "or an Xcode project, and no teamId in asc.properties")
        return out


# --- API -------------------------------------------------------------------

def token(config: Config) -> str:
    """An ES256-signed JWT. The whole protocol is this plus REST.

    JOSE wants the signature as a raw r||s pair; the cryptography library, like
    OpenSSL, hands back DER, so it has to be unpacked. Getting this wrong
    produces a 401 that looks like a bad key.
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, utils

    def b64(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    now = int(time.time())
    header = b64(json.dumps({"alg": "ES256", "kid": config.key_id, "typ": "JWT"}).encode())
    payload = b64(json.dumps(
        {"iss": config.issuer, "iat": now, "exp": now + 900, "aud": "appstoreconnect-v1"}
    ).encode())
    signing_input = header + b"." + payload

    key = serialization.load_pem_private_key(config.key_path.read_bytes(), password=None)
    der = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    signature = b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return (signing_input + b"." + signature).decode()


def asc(config: Config, method: str, path: str, body: dict | None = None,
        attempts: int = 3) -> dict:
    """One REST call, retrying Apple's own 5xx.

    Not defensive padding: the first real run of --sign here got a 500 from
    POST /v1/profiles on a call that had succeeded twice minutes earlier. That
    matters more than it looks, because ensure_signing deletes the old profile
    before creating the new one, so a create that fails leaves the account with
    no profile at all and the next archive fails somewhere less obvious.

    Only 5xx and only idempotent-in-practice calls benefit, but every call here
    is safe to repeat: the writes are create-or-replace against a name we own.
    """
    data = json.dumps(body).encode() if body else None
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(f"{API}{path}", data=data, method=method)
        # A fresh token per attempt: they expire in 15 minutes and a retry
        # after a long backoff should not fail on a stale one.
        request.add_header("Authorization", f"Bearer {token(config)}")
        request.add_header("Content-Type", "application/json")
        try:
            # A timeout, so a stalled connection fails instead of hanging the
            # run: --testflight once sat for ten minutes on one GET.
            with urllib.request.urlopen(request, timeout=60) as response:
                text = response.read().decode()
            return json.loads(text) if text else {}
        except urllib.error.HTTPError as error:
            text = error.read().decode()
            if error.code >= 500 and attempt < attempts:
                print(f"{DIM}   {method} {path} -> {error.code}, retrying "
                      f"({attempt}/{attempts - 1}){RESET}")
                time.sleep(2 * attempt)
                continue
            detail = ""
            try:
                errors = json.loads(text).get("errors", [])
                detail = "; ".join(e.get("detail") or e.get("title", "") for e in errors)
            except json.JSONDecodeError:
                detail = text[:200]
            raise SystemExit(
                f"App Store Connect {method} {path} -> {error.code}"
                + (f": {detail}" if detail else "")
            )
        except (TimeoutError, urllib.error.URLError) as error:
            if attempt < attempts:
                print(f"{DIM}   {method} {path} -> {error}, retrying "
                      f"({attempt}/{attempts - 1}){RESET}")
                time.sleep(2 * attempt)
                continue
            raise SystemExit(f"App Store Connect {method} {path}: {error}")
    raise SystemExit(f"App Store Connect {method} {path}: exhausted {attempts} attempts")


def fingerprint(certificate_content: str) -> str:
    """Certificates are matched to keychain identities by SHA-1 fingerprint,
    which is exactly what `security find-identity` prints as an identity's id."""
    return hashlib.sha1(base64.b64decode(certificate_content)).hexdigest().upper()


# --- keychain and certificate ----------------------------------------------

def ensure_keychain() -> str:
    """A dedicated keychain, not the login one.

    A key imported into the login keychain has no partition list, and macOS then
    refuses to hand it to codesign without a GUI prompt -- the build fails with
    errSecInternalComponent, which names nothing about keychains. Setting a
    partition list needs that keychain's password, and the login password is not
    ours to have; a keychain created here has a password we generated, so the
    whole thing stays headless.
    """
    SIGNING_DIR.mkdir(parents=True, exist_ok=True)
    if not KEYCHAIN_PASSWORD_FILE.exists():
        KEYCHAIN_PASSWORD_FILE.write_text(secrets.token_hex(24))
        KEYCHAIN_PASSWORD_FILE.chmod(0o600)
    password = KEYCHAIN_PASSWORD_FILE.read_text().strip()

    if not pathlib.Path(f"{KEYCHAIN}-db").exists():
        sh("security", "create-keychain", "-p", password, str(KEYCHAIN))
    # No arguments means no auto-lock: a keychain that relocks part way through
    # an archive fails exactly like one that was never unlocked.
    sh("security", "set-keychain-settings", str(KEYCHAIN))
    sh("security", "unlock-keychain", "-p", password, str(KEYCHAIN))

    # Preserve the existing search list -- codesign only looks at keychains on
    # it, and the login keychain has to stay there.
    listed = re.findall(r'"([^"]+)"', sh("security", "list-keychains", "-d", "user"))
    if not any(path.endswith("skein-signing.keychain-db") for path in listed):
        sh("security", "list-keychains", "-d", "user", "-s", str(KEYCHAIN), *listed)
    return password


def keychain_identities() -> list[tuple[str, str]]:
    out = sh("security", "find-identity", "-v", "-p", "codesigning")
    return re.findall(r'\)\s+([0-9A-F]{40})\s+"(.+)"', out)


def create_distribution_certificate(config: Config, existing_count: int) -> tuple[str, str]:
    """A certificate is a private key plus Apple's signature over a request for
    it. The key is generated here and never leaves the machine; only the CSR
    goes up."""
    SIGNING_DIR.mkdir(parents=True, exist_ok=True)
    key_path = SIGNING_DIR / "distribution.key.pem"
    csr_path = SIGNING_DIR / "distribution.csr.pem"
    cer_path = SIGNING_DIR / "distribution.cer"
    pem_path = SIGNING_DIR / "distribution.pem"
    p12_path = SIGNING_DIR / "distribution.p12"

    sh(OPENSSL, "req", "-new", "-newkey", "rsa:2048", "-nodes",
       "-keyout", str(key_path), "-out", str(csr_path),
       "-subj", "/CN=App Store Distribution/O=Gelopard/C=SE")
    key_path.chmod(0o600)

    try:
        created = asc(config, "POST", "/v1/certificates", {
            "data": {
                "type": "certificates",
                "attributes": {
                    "certificateType": "DISTRIBUTION",
                    "csrContent": csr_path.read_text(),
                },
            }
        })
    except SystemExit as error:
        if existing_count > 0:
            raise SystemExit(
                f"{error}\n      the team has {existing_count} distribution certificate(s) already "
                "and none has a private key on this machine -- revoke an unused one in "
                "Certificates, Identifiers & Profiles, then re-run"
            )
        raise

    attributes = created["data"]["attributes"]
    cer_path.write_bytes(base64.b64decode(attributes["certificateContent"]))
    sh(OPENSSL, "x509", "-inform", "DER", "-in", str(cer_path), "-out", str(pem_path))
    sh(OPENSSL, "pkcs12", "-export", "-inkey", str(key_path), "-in", str(pem_path),
       "-out", str(p12_path), "-passout", "pass:skein", "-name", "App Store Distribution")
    p12_path.chmod(0o600)

    password = ensure_keychain()
    # -A, then a partition list: the first says any application may use the key,
    # the second is what actually stops macOS prompting for confirmation.
    sh("security", "import", str(p12_path), "-k", str(KEYCHAIN), "-P", "skein", "-A")
    sh("security", "set-key-partition-list",
       "-S", "apple-tool:,apple:,codesign:", "-s", "-k", password, str(KEYCHAIN))

    sha1 = fingerprint(attributes["certificateContent"])
    if not any(identity[0] == sha1 for identity in keychain_identities()):
        raise SystemExit(
            f"imported the distribution certificate but {KEYCHAIN}-db does not list it "
            "as a signing identity"
        )
    return created["data"]["id"], sha1


def distribution_certificate(config: Config) -> tuple[str, str, bool]:
    # Unlock first: a locked keychain hides its identities, and the certificate
    # would be reissued on every run instead of reused.
    if pathlib.Path(f"{KEYCHAIN}-db").exists():
        ensure_keychain()
    certificates = asc(
        config, "GET", "/v1/certificates?filter[certificateType]=DISTRIBUTION&limit=200"
    ).get("data", [])
    identities = {identity[0] for identity in keychain_identities()}
    for certificate in certificates:
        sha1 = fingerprint(certificate["attributes"]["certificateContent"])
        if sha1 in identities:
            return certificate["id"], sha1, True
    certificate_id, sha1 = create_distribution_certificate(config, len(certificates))
    return certificate_id, sha1, False


# --- commands --------------------------------------------------------------

def ensure_signing(config: Config) -> str:
    """Provision the certificate and an App Store profile for this bundle id.

    Idempotent: a distribution certificate whose private key is already in the
    shared keychain is reused, and the profile is reissued every run because it
    is free to and a stale one fails at signing time with a mismatch that reads
    like a project misconfiguration.
    """
    bundles = asc(
        config, "GET",
        f"/v1/bundleIds?filter[identifier]={urllib.parse.quote(config.bundle_id)}&limit=2"
    ).get("data", [])
    bundle = next(
        (b for b in bundles if b["attributes"]["identifier"] == config.bundle_id), None
    )
    if not bundle:
        fail(
            f"bundle id {config.bundle_id} is not registered",
            "create it in Certificates, Identifiers & Profiles -- the API cannot",
        )

    certificate_id, sha1, reused = distribution_certificate(config)
    step(f"distribution certificate {sha1[:8]} ({'reused' if reused else 'created'})")

    # filter[name] is NOT an exact match -- Apple matches it as a substring, so
    # "Foo App Store" also returns "com.example.foo App Store". This deletes
    # before it creates, so trusting the filter would delete another app's
    # profile whenever one name contains another. Filter exactly, here.
    for profile in asc(
        config, "GET",
        f"/v1/profiles?filter[name]={urllib.parse.quote(config.profile_name)}&limit=50"
    ).get("data", []):
        if profile["attributes"]["name"] != config.profile_name:
            continue
        asc(config, "DELETE", f"/v1/profiles/{profile['id']}")

    created = asc(config, "POST", "/v1/profiles", {
        "data": {
            "type": "profiles",
            "attributes": {"name": config.profile_name, "profileType": "IOS_APP_STORE"},
            "relationships": {
                "bundleId": {"data": {"type": "bundleIds", "id": bundle["id"]}},
                "certificates": {"data": [{"type": "certificates", "id": certificate_id}]},
            },
        }
    })

    attributes = created["data"]["attributes"]
    content = base64.b64decode(attributes["profileContent"])
    for directory in PROFILE_DIRS:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{attributes['uuid']}.mobileprovision").write_bytes(content)
    step(f'profile "{config.profile_name}" installed ({attributes["uuid"]})')
    return config.profile_name


def find_app(config: Config) -> dict:
    """The app record for this bundle id, or a clear failure.

    There is no POST /v1/apps -- app records are created in the App Store
    Connect console and nowhere else -- so this never creates, only reports.
    """
    app = next((a for a in asc(
        config, "GET",
        f"/v1/apps?filter[bundleId]={urllib.parse.quote(config.bundle_id)}&limit=2"
    ).get("data", []) if a["attributes"]["bundleId"] == config.bundle_id), None)
    if not app:
        fail(f"no App Store Connect app for {config.bundle_id}",
             "the app record is created in App Store Connect, not through this API")
    return app


def internal_group(config: Config, app: dict) -> dict:
    """The internal beta group, created if missing."""
    groups = asc(config, "GET", f"/v1/apps/{app['id']}/betaGroups?limit=50").get("data", [])
    group = next((g for g in groups
                  if g["attributes"]["isInternalGroup"]
                  and g["attributes"]["name"] == GROUP_NAME), None)
    if group:
        return group
    return asc(config, "POST", "/v1/betaGroups", {
        "data": {
            "type": "betaGroups",
            "attributes": {"name": GROUP_NAME, "isInternalGroup": True},
            "relationships": {"app": {"data": {"type": "apps", "id": app["id"]}}},
        }
    })["data"]


def add_tester(config: Config, email: str) -> None:
    """Put one person in the internal group.

    Internal testers have to be App Store Connect users on this team, so the
    name is taken from the user record rather than guessed -- and a typo'd
    address fails here, naming the account, instead of silently inviting
    nobody.

    This sends an invitation email, which is why it is its own flag and not
    something --testflight does on the way past.
    """
    users = asc(config, "GET", "/v1/users?limit=100"
                "&fields[users]=username,firstName,lastName,roles,allAppsVisible"
                ).get("data", [])
    user = next((u for u in users
                 if u["attributes"]["username"].lower() == email.lower()), None)
    if not user:
        known = ", ".join(u["attributes"]["username"] for u in users)
        fail(f"{email} is not an App Store Connect user on this team",
             f"internal testers must be; team users are: {known}")

    app = find_app(config)

    # An internal tester has to be able to see the app, and a user with
    # allAppsVisible false sees only the apps explicitly granted to them.
    # Without this check the assignment fails with a bare 409 "Tester(s) cannot
    # be assigned", which names neither the user nor the reason -- it reads like
    # a problem with the tester record. It is not; it is a permissions gap, and
    # granting access is a deliberate act rather than something a publish tool
    # should do behind your back.
    if not user["attributes"].get("allAppsVisible"):
        visible = asc(config, "GET",
                      f"/v1/users/{user['id']}/visibleApps?limit=100&fields[apps]=name"
                      ).get("data", [])
        if not any(v["id"] == app["id"] for v in visible):
            names = ", ".join(v["attributes"]["name"] for v in visible) or "none"
            fail(f"{email} cannot see {app['attributes']['name']} in App Store Connect",
                 f"they are a {'/'.join(user['attributes'].get('roles', []))} who sees: "
                 f"{names}. Grant access under Users and Access, or add the app to "
                 "their visible apps, then re-run")

    group = internal_group(config, app)

    existing = asc(config, "GET",
                   f"/v1/betaGroups/{group['id']}/betaTesters?limit=200"
                   "&fields[betaTesters]=email").get("data", [])
    if any(t["attributes"]["email"].lower() == email.lower() for t in existing):
        step(f'{email} is already in "{GROUP_NAME}"')
        return

    attributes = {"email": email}
    for field in ("firstName", "lastName"):
        if user["attributes"].get(field):
            attributes[field] = user["attributes"][field]
    asc(config, "POST", "/v1/betaTesters", {
        "data": {
            "type": "betaTesters",
            "attributes": attributes,
            "relationships": {
                "betaGroups": {"data": [{"type": "betaGroups", "id": group["id"]}]}
            },
        }
    })
    step(f'{email} added to "{GROUP_NAME}" -- Apple emails the invitation')


def register_bundle_id(config: Config) -> None:
    """Register the bundle id under Certificates, Identifiers & Profiles.

    Separate from --sign, and not implied by it, because this is the one call
    here that writes something to the Apple account which cannot be taken back:
    Apple will not delete a bundle id once an app record uses it. Signing is
    idempotent and re-runnable; this is neither, so it asks to be typed.

    The app record itself is console-only -- there is no POST /v1/apps -- so
    this unblocks that step rather than replacing it.
    """
    existing = asc(
        config, "GET",
        f"/v1/bundleIds?filter[identifier]={urllib.parse.quote(config.bundle_id)}&limit=2"
    ).get("data", [])
    if any(b["attributes"]["identifier"] == config.bundle_id for b in existing):
        step(f"bundle id {config.bundle_id} already registered")
        return

    # The name is a label in the developer portal, not the App Store name, and
    # Apple rejects anything but alphanumerics and spaces in it. Derived from
    # the store name so the portal lists the id under the right game (a
    # literal here once registered com.gelopard.tillsvidare as "Universes").
    label = re.sub(r"[^A-Za-z0-9 ]", "", listing_text("ios-name.txt")).strip() or "App"
    created = asc(config, "POST", "/v1/bundleIds", {
        "data": {
            "type": "bundleIds",
            "attributes": {
                "identifier": config.bundle_id,
                "name": label,
                "platform": "IOS",
            },
        }
    })
    step(f"registered {created['data']['attributes']['identifier']} "
         f"({created['data']['id']})")


# --- Listing ---------------------------------------------------------------

# Which store/screenshots/<dir>/ feeds which App Store Connect slot. Apple's
# names, not the marketing sizes: 6.9-inch phones go in APP_IPHONE_67 (there is
# no APP_IPHONE_69, the API rejects it), and the 13-inch iPad in the
# "12.9-inch 3rd gen" slot. A directory that does not exist is skipped, so an
# iPhone-only game carries no iPad set and a universal one must.
SCREENSHOT_SLOTS = {
    "iphone-6.9": "APP_IPHONE_67",
    "iphone-6.7": "APP_IPHONE_67",
    "iphone-6.5": "APP_IPHONE_65",
    "ipad-13": "APP_IPAD_PRO_3GEN_129",
    "ipad-12.9": "APP_IPAD_PRO_3GEN_129",
}

# The listing convention's files (gelopard/playbook, conventions/store-listing.md)
# and the App Store Connect attribute each one feeds.
LISTING_DIR = ROOT / "store" / "listing"


def listing_dir_for(locale: str) -> pathlib.Path | None:
    """store/listing/<language>/ when the repo keeps a translation for the locale.

    The directories are named after Play's language codes (sv-SE, de-DE),
    because the Play publisher got there first and one translation should feed
    both stores. Apple's locales are shorter for most languages (sv, de, but
    en-US, pt-BR), so an exact directory is tried first and then any directory
    whose language part matches. None for the default listing at the root.
    """
    exact = LISTING_DIR / locale
    if exact.is_dir():
        return exact
    language = locale.split("-")[0]
    matches = sorted(d for d in LISTING_DIR.iterdir() if d.is_dir() and d.name.split("-")[0] == language)
    return matches[0] if matches else None


def listing_text(name: str, locale: str | None = None) -> str:
    """One listing field, from the locale's directory when it has it, else the root.

    The fallback is deliberate and differs from the Play publisher, which
    refuses a half-translated listing: Apple's per-locale set is larger (name,
    subtitle, keywords, description, what's-new, privacy URL) and several of
    those are the same string in every language. ios-name.txt is the obvious
    one; a translation carries only what actually differs.
    """
    base = listing_dir_for(locale) if locale else None
    if base is not None and (base / name).exists():
        return (base / name).read_text(encoding="utf-8").rstrip("\n")
    path = LISTING_DIR / name
    if not path.exists():
        fail(f"{path.relative_to(ROOT)} not found",
             "the listing convention keeps one field per file under store/listing/")
    return path.read_text(encoding="utf-8").rstrip("\n")


def version_string() -> str:
    """The marketing version, from wherever release-please keeps it."""
    package = ROOT / "package.json"
    if package.exists():
        return json.loads(package.read_text())["version"]
    version_txt = ROOT / "version.txt"
    if version_txt.exists():
        return version_txt.read_text().strip()
    fail("no package.json or version.txt to read the version from")
    return ""


def upload_asset(config: Config, type_name: str, path: str, attributes: dict,
                 relationships: dict, data: bytes) -> None:
    """Apple's three-step asset upload: reserve, PUT the parts, commit with a checksum."""
    reserved = asc(config, "POST", path, {"data": {
        "type": type_name, "attributes": attributes, "relationships": relationships}})
    for op in reserved["data"]["attributes"].get("uploadOperations") or []:
        request = urllib.request.Request(
            op["url"], data=data[op["offset"]:op["offset"] + op["length"]], method=op["method"])
        for header in op.get("requestHeaders") or []:
            request.add_header(header["name"], header["value"])
        with urllib.request.urlopen(request, timeout=300) as response:
            response.read()
    asc(config, "PATCH", f"{path}/{reserved['data']['id']}", {"data": {
        "type": type_name, "id": reserved["data"]["id"],
        "attributes": {"uploaded": True, "sourceFileChecksum": hashlib.md5(data).hexdigest()}}})


def push_listing(config: Config, locale: str) -> None:
    """Push store/listing/ and store/screenshots/ to App Store Connect.

    Copy that only reaches the store by being pasted into a console is a second
    copy, and within a week nobody can say which is current. This makes the
    repo the listing: name, subtitle and privacy policy on the app info; the
    editable version's string, description and keywords (and what's-new once a
    version has shipped, since Apple refuses the field on a first version);
    categories when asc.properties names them; and every screenshot set the
    repo carries, replaced whole so the console shows exactly the repo's files
    in filename order. Re-running overwrites. It never submits for review.

    A second locale (--locale sv) reads each field from store/listing/<language>/
    when that directory has it and from the root otherwise, so the Swedish page
    is reproducible from the repo like the English one; screenshots are the
    same set for every locale.
    """
    props = properties()
    app = find_app(config)
    step(f"app {app['attributes']['name']} ({app['id']})")
    translation = listing_dir_for(locale)
    if translation is not None:
        step(f"locale {locale} from {translation.relative_to(ROOT)}/, the rest from store/listing/")

    # Once a version is on sale the app has two appInfos: the live one, which
    # refuses edits (409), and the one riding with the next version. Edit that.
    app_infos = asc(config, "GET", f"/v1/apps/{app['id']}/appInfos?limit=10")["data"]
    app_info = next((i for i in app_infos
                     if i["attributes"].get("state") != "READY_FOR_DISTRIBUTION"), app_infos[0])
    info_locs = asc(config, "GET",
                    f"/v1/appInfos/{app_info['id']}/appInfoLocalizations?limit=50")["data"]
    info_loc = next((l for l in info_locs if l["attributes"]["locale"] == locale), None)
    info_attrs = {
        "name": listing_text("ios-name.txt", locale),
        "subtitle": listing_text("ios-subtitle.txt", locale),
        "privacyPolicyUrl": listing_text("privacy-policy-url.txt", locale),
    }
    if info_loc:
        asc(config, "PATCH", f"/v1/appInfoLocalizations/{info_loc['id']}", {"data": {
            "type": "appInfoLocalizations", "id": info_loc["id"], "attributes": info_attrs}})
    else:
        asc(config, "POST", "/v1/appInfoLocalizations", {"data": {
            "type": "appInfoLocalizations",
            "attributes": {"locale": locale, **info_attrs},
            "relationships": {"appInfo": {"data": {"type": "appInfos", "id": app_info["id"]}}}}})
    step(f"name \"{info_attrs['name']}\", subtitle \"{info_attrs['subtitle']}\" ({locale})")

    categories = {key: props.get(key) for key in
                  ("primaryCategory", "primarySubcategoryOne", "primarySubcategoryTwo")
                  if props.get(key)}
    if categories:
        asc(config, "PATCH", f"/v1/appInfos/{app_info['id']}", {"data": {
            "type": "appInfos", "id": app_info["id"],
            "relationships": {key: {"data": {"type": "appCategories", "id": value}}
                              for key, value in categories.items()}}})
        step("categories " + ", ".join(categories.values()))

    versions = asc(config, "GET", f"/v1/apps/{app['id']}/appStoreVersions?limit=20"
                   "&fields[appStoreVersions]=versionString,appStoreState,platform")["data"]
    shipped = any(v["attributes"]["appStoreState"] == "READY_FOR_SALE" for v in versions)
    editable = next((v for v in versions if v["attributes"]["appStoreState"] != "READY_FOR_SALE"
                     and v["attributes"].get("platform", "IOS") == "IOS"), None)
    wanted = version_string()
    if not editable:
        editable = asc(config, "POST", "/v1/appStoreVersions", {"data": {
            "type": "appStoreVersions",
            "attributes": {"platform": "IOS", "versionString": wanted},
            "relationships": {"app": {"data": {"type": "apps", "id": app["id"]}}}}})["data"]
        step(f"created version {wanted}")
    elif editable["attributes"]["versionString"] != wanted:
        asc(config, "PATCH", f"/v1/appStoreVersions/{editable['id']}", {"data": {
            "type": "appStoreVersions", "id": editable["id"],
            "attributes": {"versionString": wanted}}})
        step(f"version {editable['attributes']['versionString']} -> {wanted}")

    version_locs = asc(config, "GET",
                       f"/v1/appStoreVersions/{editable['id']}/appStoreVersionLocalizations?limit=50")["data"]
    version_loc = next((l for l in version_locs if l["attributes"]["locale"] == locale), None)
    version_attrs = {
        "description": listing_text("full-description.txt", locale),
        "keywords": listing_text("ios-keywords.txt", locale),
    }
    if shipped:
        version_attrs["whatsNew"] = listing_text("whats-new.txt", locale)
    if version_loc:
        asc(config, "PATCH", f"/v1/appStoreVersionLocalizations/{version_loc['id']}", {"data": {
            "type": "appStoreVersionLocalizations", "id": version_loc["id"],
            "attributes": version_attrs}})
    else:
        version_loc = asc(config, "POST", "/v1/appStoreVersionLocalizations", {"data": {
            "type": "appStoreVersionLocalizations",
            "attributes": {"locale": locale, **version_attrs},
            "relationships": {"appStoreVersion": {"data": {
                "type": "appStoreVersions", "id": editable["id"]}}}}})["data"]
    step(f"description ({len(version_attrs['description'])} chars) and keywords")

    sets = asc(config, "GET",
               f"/v1/appStoreVersionLocalizations/{version_loc['id']}/appScreenshotSets?limit=50")["data"]
    for existing in sets:
        asc(config, "DELETE", f"/v1/appScreenshotSets/{existing['id']}")
    for directory, display_type in SCREENSHOT_SLOTS.items():
        folder = ROOT / "store" / "screenshots" / directory
        if not folder.is_dir():
            continue
        files = sorted(f for f in folder.iterdir() if f.suffix == ".png")
        if not files:
            continue
        screenshot_set = asc(config, "POST", "/v1/appScreenshotSets", {"data": {
            "type": "appScreenshotSets",
            "attributes": {"screenshotDisplayType": display_type},
            "relationships": {"appStoreVersionLocalization": {"data": {
                "type": "appStoreVersionLocalizations", "id": version_loc["id"]}}}}})["data"]
        for file in files:
            data = file.read_bytes()
            upload_asset(config, "appScreenshots", "/v1/appScreenshots",
                         {"fileSize": len(data), "fileName": file.name},
                         {"appScreenshotSet": {"data": {
                             "type": "appScreenshotSets", "id": screenshot_set["id"]}}},
                         data)
        step(f"{display_type}: {len(files)} screenshots from store/screenshots/{directory}/")
    print(f"\n{GREEN}listing pushed{RESET} {DIM}-- App Store Connect matches store/listing/ "
          f"and store/screenshots/; nothing submitted for review{RESET}")


def write_export_options(config: Config, path: pathlib.Path) -> None:
    """Godot writes its own ExportOptions.plist with automatic signing and an
    empty profile, and regenerates it on every export, so it is never the one to
    archive with. This replaces it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        plistlib.dump({
            "method": "app-store-connect",
            "teamID": config.team_id,
            "signingStyle": "manual",
            "signingCertificate": "Apple Distribution",
            "provisioningProfiles": {config.bundle_id: config.profile_name},
            "uploadSymbols": True,
            "destination": "export",
        }, handle)
    # Shown repo-relative when it is inside the repo, which is where the
    # Makefile puts it, and as given when it is not.
    try:
        shown = path.resolve().relative_to(ROOT)
    except ValueError:
        shown = path
    step(f"export options written to {shown}")


def upload(config: Config, ipa: pathlib.Path) -> None:
    """Validate before uploading: it catches the rejections that would otherwise
    arrive by email half an hour after the upload appeared to succeed."""
    if not ipa.exists():
        fail(f"no .ipa at {ipa}", "build one first with: make ios MODE=release")
    # altool finds the .p8 itself, by id, in ~/.appstoreconnect/private_keys.
    args = ["-f", str(ipa), "-t", "ios", "--apiKey", config.key_id, "--apiIssuer", config.issuer]
    print(f"{DIM}$ xcrun altool --validate-app ...{RESET}")
    subprocess.run(["xcrun", "altool", "--validate-app", *args], check=True)
    print(f"{DIM}$ xcrun altool --upload-app ...{RESET}")
    subprocess.run(["xcrun", "altool", "--upload-app", *args], check=True)
    step("uploaded -- processing takes a few minutes before it appears in TestFlight")


def testflight(config: Config) -> None:
    """Put the latest processed build in front of internal testers.

    Upload and distribution are two separate acts because Apple processes the
    binary first. Safe to re-run: the group is created only if missing and a
    build already in it is left alone. Internal testers are App Store Connect
    users, so no Beta App Review stands between an upload and their install.
    """
    app = find_app(config)

    builds = asc(config, "GET", (
        f"/v1/builds?filter[app]={app['id']}&limit=20&sort=-uploadedDate"
        "&fields[builds]=version,processingState,usesNonExemptEncryption,expired"
    )).get("data", [])
    # Newest first (the sort above): unsorted, Apple's order is not upload
    # order, and the first VALID build can be several releases old.
    build = next((b for b in builds
                  if b["attributes"]["processingState"] == "VALID"
                  and not b["attributes"]["expired"]), None)
    if not build:
        latest = builds[0]["attributes"] if builds else None
        fail("no processed build to distribute",
             f"latest is {latest['version']} ({latest['processingState']}) -- "
             "processing takes a few minutes" if latest else
             "upload one with: make ios-upload")
    step(f"build {build['attributes']['version']}")

    # A build made before ITSAppUsesNonExemptEncryption reached Info.plist stops
    # here and waits to be asked in the UI. Answering it here is the same answer.
    if build["attributes"]["usesNonExemptEncryption"] is None:
        asc(config, "PATCH", f"/v1/builds/{build['id']}", {
            "data": {"type": "builds", "id": build["id"],
                     "attributes": {"usesNonExemptEncryption": False}}
        })
        step("export compliance answered")

    group = internal_group(config, app)

    existing = asc(config, "GET",
                   f"/v1/betaGroups/{group['id']}/builds?limit=50&fields[builds]=version"
                   ).get("data", [])
    if any(entry["id"] == build["id"] for entry in existing):
        step(f'already distributed to "{GROUP_NAME}"')
    else:
        asc(config, "POST", f"/v1/betaGroups/{group['id']}/relationships/builds",
            {"data": [{"type": "builds", "id": build["id"]}]})
        step(f'distributed to "{GROUP_NAME}"')

    testers = asc(config, "GET",
                  f"/v1/betaGroups/{group['id']}/betaTesters?limit=50"
                  "&fields[betaTesters]=email").get("data", [])
    emails = ", ".join(t["attributes"]["email"] for t in testers) or "none yet"
    print(f"\n{GREEN}internal testing ready{RESET} {DIM}-- {len(testers)} tester(s): {emails}{RESET}")


def editable_version(config: Config, app: dict) -> dict:
    """The App Store version that can still be edited, created from the repo's
    version string when the app has none. Once a version is on sale the live
    one refuses edits, so the next one is the target."""
    versions = asc(config, "GET", f"/v1/apps/{app['id']}/appStoreVersions?limit=20"
                   "&fields[appStoreVersions]=versionString,appStoreState,platform,releaseType")["data"]
    editable = next((v for v in versions if v["attributes"]["appStoreState"] != "READY_FOR_SALE"
                     and v["attributes"].get("platform", "IOS") == "IOS"), None)
    if editable:
        return editable
    wanted = version_string()
    created = asc(config, "POST", "/v1/appStoreVersions", {"data": {
        "type": "appStoreVersions",
        "attributes": {"platform": "IOS", "versionString": wanted},
        "relationships": {"app": {"data": {"type": "apps", "id": app["id"]}}}}})["data"]
    step(f"created version {wanted}")
    return created


def declare(config: Config) -> None:
    """The declarations a first submission needs, from the terminal.

    Age rating (every category NONE, every boolean false: the 4+ answer, with
    kidsAgeBand left null so the app never enters the Kids Category, a one-way
    door), free pricing, availability in every territory, content rights,
    manual release, and the review contact when asc.properties carries one.
    Everything here is a PATCH or a create-if-missing, so re-running is safe,
    and every write is read back. App Privacy has no API and stays a console
    step; the summary at the end says so.

    The shapes Apple wants are the ones playbook conventions/store-publishing.md
    recorded from the first submission: compound creates take the literal
    ${local-id} syntax for their included resources, free pricing is the price
    point with customerPrice 0 in the base territory, and review details need a
    phone in +<country code> form or the create is refused.

    asc.properties keys read here, all optional:
      reviewFirstName, reviewLastName, reviewEmail, reviewPhone
      contentRights   DOES_NOT_USE_THIRD_PARTY_CONTENT (default) | USES_THIRD_PARTY_CONTENT
      releaseType     MANUAL (default) | AFTER_APPROVAL
    Review notes come from store/review-notes.txt when the file exists (outside
    store/listing/, whose validator allows only the listing fields).
    """
    props = properties()
    app = find_app(config)
    step(f"app {app['attributes']['name']} ({app['id']})")

    # Age rating: rather than name Apple's categories, which the 2025 questionnaire
    # changed once already, read the declaration and turn every enum to NONE and
    # every boolean to false. kidsAgeBand and other nulls are left alone.
    app_infos = asc(config, "GET", f"/v1/apps/{app['id']}/appInfos?limit=10")["data"]
    app_info = next((i for i in app_infos
                     if i["attributes"].get("state") != "READY_FOR_DISTRIBUTION"), app_infos[0])
    rating = asc(config, "GET", f"/v1/appInfos/{app_info['id']}/ageRatingDeclaration")["data"]
    wanted = {}
    for key, value in rating["attributes"].items():
        if key == "kidsAgeBand" or value is None:
            continue
        if isinstance(value, bool):
            wanted[key] = False
        elif isinstance(value, str):
            wanted[key] = "NONE"
    changed = {k: v for k, v in wanted.items() if rating["attributes"].get(k) != v}
    if changed:
        asc(config, "PATCH", f"/v1/ageRatingDeclarations/{rating['id']}", {"data": {
            "type": "ageRatingDeclarations", "id": rating["id"], "attributes": changed}})
        step(f"age rating: {len(changed)} field(s) set to none/false, kidsAgeBand null (4+)")
    else:
        step("age rating already none/false everywhere (4+)")

    # Pricing: free, from the USA base territory's zero price point.
    schedule = None
    try:
        schedule = asc(config, "GET", f"/v1/apps/{app['id']}/appPriceSchedule"
                       "?include=manualPrices,baseTerritory").get("data")
    except SystemExit as error:
        if "404" not in str(error):
            raise
    if schedule:
        step("price schedule exists; left as it is")
    else:
        points = asc(config, "GET", f"/v1/apps/{app['id']}/appPricePoints"
                     "?filter[territory]=USA&limit=200"
                     "&fields[appPricePoints]=customerPrice,proceeds")["data"]
        free = next((pt for pt in points
                     if float(pt["attributes"]["customerPrice"]) == 0), None)
        if not free:
            fail("no free price point for USA", "Apple's price points changed shape; look at "
                 "/v1/apps/{id}/appPricePoints?filter[territory]=USA")
        asc(config, "POST", "/v1/appPriceSchedules", {
            "data": {"type": "appPriceSchedules", "relationships": {
                "app": {"data": {"type": "apps", "id": app["id"]}},
                "baseTerritory": {"data": {"type": "territories", "id": "USA"}},
                "manualPrices": {"data": [{"type": "appPrices", "id": "${price-free}"}]}}},
            "included": [{"type": "appPrices", "id": "${price-free}",
                          "attributes": {"startDate": None},
                          "relationships": {"appPricePoint": {"data": {
                              "type": "appPricePoints", "id": free["id"]}}}}]})
        step("price: free (USA base territory, price point with customerPrice 0)")

    # Availability: every territory, and new ones as Apple adds them.
    availability = None
    try:
        availability = asc(config, "GET", f"/v1/apps/{app['id']}/appAvailabilityV2"
                           "?fields[appAvailabilities]=availableInNewTerritories").get("data")
    except SystemExit as error:
        if "404" not in str(error):
            raise
    if availability:
        step("availability exists; left as it is")
    else:
        territories = asc(config, "GET", "/v1/territories?limit=200")["data"]
        ids = [t["id"] for t in territories]
        asc(config, "POST", "/v2/appAvailabilities", {
            "data": {"type": "appAvailabilities",
                     "attributes": {"availableInNewTerritories": True},
                     "relationships": {
                         "app": {"data": {"type": "apps", "id": app["id"]}},
                         "territoryAvailabilities": {"data": [
                             {"type": "territoryAvailabilities", "id": f"${{t-{t}}}"} for t in ids]}}},
            "included": [{"type": "territoryAvailabilities", "id": f"${{t-{t}}}",
                          "attributes": {"available": True},
                          "relationships": {"territory": {"data": {
                              "type": "territories", "id": t}}}} for t in ids]})
        step(f"availability: all {len(ids)} territories, and new ones")

    # Content rights, on the app.
    rights = props.get("contentRights") or "DOES_NOT_USE_THIRD_PARTY_CONTENT"
    asc(config, "PATCH", f"/v1/apps/{app['id']}", {"data": {
        "type": "apps", "id": app["id"], "attributes": {"contentRightsDeclaration": rights}}})
    step(f"content rights: {rights}")

    # Release type, on the editable version.
    version = editable_version(config, app)
    release = props.get("releaseType") or "MANUAL"
    asc(config, "PATCH", f"/v1/appStoreVersions/{version['id']}", {"data": {
        "type": "appStoreVersions", "id": version["id"], "attributes": {"releaseType": release}}})
    step(f"version {version['attributes']['versionString']}: release type {release}")

    # Review contact, when the properties carry one. A phone is not optional in
    # practice: the create is refused with a 409 without it.
    contact = {key: props.get(key, "") for key in
               ("reviewFirstName", "reviewLastName", "reviewEmail", "reviewPhone")}
    if all(contact.values()):
        notes_file = ROOT / "store" / "review-notes.txt"
        attrs = {
            "contactFirstName": contact["reviewFirstName"],
            "contactLastName": contact["reviewLastName"],
            "contactEmail": contact["reviewEmail"],
            "contactPhone": contact["reviewPhone"],
            "demoAccountRequired": False,
        }
        if notes_file.exists():
            attrs["notes"] = notes_file.read_text(encoding="utf-8").strip()
        detail = None
        try:
            detail = asc(config, "GET",
                         f"/v1/appStoreVersions/{version['id']}/appStoreReviewDetail").get("data")
        except SystemExit as error:
            if "404" not in str(error):
                raise
        if detail:
            asc(config, "PATCH", f"/v1/appStoreReviewDetails/{detail['id']}", {"data": {
                "type": "appStoreReviewDetails", "id": detail["id"], "attributes": attrs}})
        else:
            asc(config, "POST", "/v1/appStoreReviewDetails", {"data": {
                "type": "appStoreReviewDetails", "attributes": attrs,
                "relationships": {"appStoreVersion": {"data": {
                    "type": "appStoreVersions", "id": version["id"]}}}}})
        step(f"review contact: {contact['reviewFirstName']} {contact['reviewLastName']}, "
             f"{contact['reviewEmail']}, notes {'from store/review-notes.txt' if 'notes' in attrs else 'none'}")
    else:
        missing = [k for k, v in contact.items() if not v]
        step(f"review contact skipped: {', '.join(missing)} not in asc.properties")

    print(f"\n{GREEN}declarations set{RESET} {DIM}-- still console only: App Privacy "
          f"(Get Started, no data collected, Save, Publish){RESET}")


def dry_run(config: Config) -> None:
    """Authenticate and read state back. Changes nothing."""
    bundles = asc(config, "GET",
                  f"/v1/bundleIds?filter[identifier]={urllib.parse.quote(config.bundle_id)}&limit=2"
                  ).get("data", [])
    apps = asc(config, "GET",
               f"/v1/apps?filter[bundleId]={urllib.parse.quote(config.bundle_id)}&limit=2"
               ).get("data", [])
    certificates = asc(config, "GET",
                       "/v1/certificates?filter[certificateType]=DISTRIBUTION&limit=200"
                       ).get("data", [])
    identities = {identity[0] for identity in keychain_identities()}
    usable = [c for c in certificates
              if fingerprint(c["attributes"]["certificateContent"]) in identities]
    profiles = [p for p in asc(
        config, "GET",
        f"/v1/profiles?filter[name]={urllib.parse.quote(config.profile_name)}&limit=50"
    ).get("data", []) if p["attributes"]["name"] == config.profile_name]

    print(f"authenticated OK as issuer {config.issuer[:8]}... with key {config.key_id}")
    print(f"  bundle id    {config.bundle_id}: "
          f"{'registered' if bundles else 'NOT REGISTERED'}")
    print(f"  app record   {'yes, ' + apps[0]['attributes']['name'] if apps else 'NONE'}")
    print(f"  certificates {len(certificates)} in team, {len(usable)} usable on this machine")
    print(f'  profile      "{config.profile_name}": {"exists" if profiles else "not yet created"}')
    print("nothing changed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="validate config without touching the network")
    parser.add_argument("--dry-run", action="store_true",
                        help="authenticate and read state, changing nothing")
    parser.add_argument("--register-bundle-id", action="store_true",
                        help="register the bundle id (writes to the Apple account; "
                             "Apple will not delete one an app record uses)")
    parser.add_argument("--sign", action="store_true",
                        help="provision the certificate and App Store profile")
    parser.add_argument("--export-options", metavar="PATH",
                        help="write a manual-signing ExportOptions.plist to PATH")
    parser.add_argument("--upload", metavar="IPA",
                        help="validate and upload an .ipa to App Store Connect")
    parser.add_argument("--add-tester", metavar="EMAIL",
                        help="add an App Store Connect user to the internal group "
                             "(Apple emails them an invitation)")
    parser.add_argument("--testflight", action="store_true",
                        help="distribute the latest processed build to internal testers")
    parser.add_argument("--listing", action="store_true",
                        help="push store/listing/ and store/screenshots/ to the editable "
                             "App Store version (never submits)")
    parser.add_argument("--declare", action="store_true",
                        help="set the age rating (4+), free price, availability in every "
                             "territory, content rights, release type and review contact")
    parser.add_argument("--locale", default=None,
                        help="listing locale for --listing (default: asc.properties locale, "
                             "else en-US)")
    args = parser.parse_args()

    config = Config()
    problems = config.problems()

    if args.check:
        if problems:
            for problem in problems:
                print(f"{RED}ERROR{RESET} {problem}")
            print(f"      {DIM}see asc.properties.example{RESET}")
            return 1
        print(f"ASC config OK: {config.bundle_id} via key {config.key_id}, "
              f"team {config.team_id}, profile \"{config.profile_name}\"")
        return 0

    if problems:
        fail("; ".join(problems), "run: make check-asc")

    if args.dry_run:
        dry_run(config)
    if args.register_bundle_id:
        register_bundle_id(config)
    if args.sign:
        ensure_signing(config)
    if args.export_options:
        write_export_options(config, pathlib.Path(args.export_options))
    if args.upload:
        upload(config, pathlib.Path(args.upload))
    if args.add_tester:
        add_tester(config, args.add_tester)
    if args.testflight:
        testflight(config)
    if args.listing:
        push_listing(config, args.locale or properties().get("locale") or "en-US")
    if args.declare:
        declare(config)

    if not any([args.dry_run, args.register_bundle_id, args.sign, args.export_options,
                args.upload, args.add_tester, args.testflight, args.listing, args.declare]):
        parser.print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
