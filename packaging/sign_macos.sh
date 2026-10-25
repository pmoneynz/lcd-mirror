#!/bin/bash
# Sign, notarize and staple the macOS app with a Developer ID.
#
#   packaging/sign_macos.sh "Developer ID Application: NAME (TEAMID)" NOTARY_PROFILE [APP]
#
# NOTARY_PROFILE is a notarytool keychain profile, made once with
#   xcrun notarytool store-credentials NOTARY_PROFILE --apple-id ID --team-id TEAMID
# (it asks for an app-specific password and keeps it in the keychain).
# Pass "-" as NOTARY_PROFILE to sign only.  APP defaults to dist/LCD Mirror.app.
set -euo pipefail
IDENTITY="$1"
PROFILE="$2"
APP="${3:-dist/LCD Mirror.app}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ENT="$HERE/entitlements.plist"

sign() { codesign --force --timestamp --options runtime --entitlements "$ENT" --sign "$IDENTITY" "$1"; }

# Inside out: every Mach-O file (libraries, extension modules, executables), deepest first,
# then the app bundle itself.  No --deep: it signs in the wrong order and skips files.
while IFS= read -r f; do
    if file -b "$f" | grep -q "Mach-O"; then sign "$f"; fi
done < <(find "$APP/Contents" -type f \( -name "*.dylib" -o -name "*.so" -o -perm -u+x \) -print \
         | awk '{ print length($0) "\t" $0 }' | sort -rn | cut -f2-)
while IFS= read -r fw; do sign "$fw"; done < <(find "$APP/Contents" -type d -name "*.framework" -print)
sign "$APP"

codesign --verify --strict --verbose=2 "$APP"
if [ "$PROFILE" = "-" ]; then
    echo "signed only (not notarized): $APP"
    exit 0
fi

ZIP="$(dirname "$APP")/notarize-upload.zip"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
xcrun notarytool submit "$ZIP" --keychain-profile "$PROFILE" --wait
rm -f "$ZIP"
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"
spctl --assess --type execute --verbose=2 "$APP"
