#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p build/icon/AppIcon.icon/Assets build/icon/compiled build/icon/AppIcon.iconset
cp Assets/Artwork/AppIcon.png build/icon/AppIcon.icon/Assets/Artwork.png
cp Assets/Artwork/AppIcon.icon.json build/icon/AppIcon.icon/icon.json
xcrun actool build/icon/AppIcon.icon --compile build/icon/compiled \
  --platform macosx --minimum-deployment-target 14.0 --app-icon AppIcon \
  --output-partial-info-plist build/icon/Info.plist --output-format human-readable-text
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" Assets/Artwork/AppIcon.png \
    --out "build/icon/AppIcon.iconset/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z "$double" "$double" Assets/Artwork/AppIcon.png \
    --out "build/icon/AppIcon.iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns build/icon/AppIcon.iconset -o build/icon/AppIcon.icns
test -s build/icon/compiled/Assets.car
test -s build/icon/AppIcon.icns
