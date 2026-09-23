# App Store Connect targets. Adjust paths to the repo, then include or paste
# into the Makefile. Requires tools/asc_publish.py (vendored from the
# app-store-publish skill) and asc.properties.

ASC          := python3 tools/asc_publish.py
IOS_EXPORT_OPTS := build/ios-build/ExportOptions.plist

check-asc:
	@$(ASC) --check

# Reads App Store Connect without changing anything.
asc-status:
	@$(ASC) --dry-run

# Signing is manual: automatic signing asks Apple for a development profile,
# which it will not issue to a team with no registered devices. --sign
# provisions an App Store profile and writes the ExportOptions.plist naming it.
ios-sign:
	@mkdir -p $(dir $(IOS_EXPORT_OPTS))
	@$(ASC) --sign --export-options $(IOS_EXPORT_OPTS)

# Validates first: it catches the rejections that otherwise arrive by email
# half an hour after the upload appeared to succeed.
ios-upload: check-asc
	@$(ASC) --upload public/universes.ipa

# Uploads nothing: hands a build Apple has finished processing to the internal
# testers. Separate because processing takes a few minutes.
ios-testflight: check-asc
	@$(ASC) --testflight

.PHONY: check-asc asc-status ios-sign ios-upload ios-testflight
