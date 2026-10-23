# Play publishing targets. Adjust AAB and the tool path to match the repo, and
# translate to npm scripts / justfile / etc. if the project does not use Make.
#
# Upload is a draft by default so that rolling out to testers stays a separate,
# deliberate step rather than a side effect of building.

PLAY_TOOL   ?= tools/play_publish.py
PLAY_AAB    ?= build/app.aab
PLAY_TRACK  ?= internal
PLAY_STATUS ?= draft

.PHONY: play-check play-status play-upload play-promote play-listing play-listing-diff

# Validates local config without touching the network.
play-check:
	@python3 $(PLAY_TOOL) --check

# Authenticates and lists the tracks. Changes nothing, and shows which
# versionCodes are already used -- worth running before every build.
play-status:
	@python3 $(PLAY_TOOL) --dry-run

# Depend on whatever target builds the bundle, so a stale .aab cannot be
# uploaded: Play rejects a versionCode it has already accepted.
play-upload: $(PLAY_AAB)
	@python3 $(PLAY_TOOL) --aab $(PLAY_AAB) --track $(PLAY_TRACK) --status $(PLAY_STATUS)

play-promote:
	@python3 $(PLAY_TOOL) --promote --track $(PLAY_TRACK)

# Listing copy, kept away from anything that ships a build: a typo fixed in a
# description should not need a new versionCode. The diff target opens an edit,
# compares the live listing against the repo and discards it, so it is safe to
# run at any time -- and it is what catches a console that has drifted.
play-listing-diff:
	@python3 $(PLAY_TOOL) --listing-dry-run

play-listing:
	@python3 $(PLAY_TOOL) --listing
