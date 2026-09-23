#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Rick Hale Parker
#
# Build the Debian / Ubuntu / Linux Mint package.
# Run from anywhere:  sh packaging/linux/build_deb.sh
# Output:             dist/p80-diagnostics_<version>_all.deb
# Needs: dpkg-deb (always on Debian-based systems). ImageMagick "convert" is
# optional; without it the 512 px icon is used as is.
set -e
TOP=$(cd "$(dirname "$0")/../.." && pwd)
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$TOP/p80diag/__init__.py")
PKG=p80-diagnostics
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

LIB=$STAGE/usr/lib/$PKG
mkdir -p "$LIB" "$STAGE/usr/bin" "$STAGE/usr/share/applications" \
         "$STAGE/usr/share/icons/hicolor/256x256/apps" "$STAGE/usr/share/doc/$PKG" "$STAGE/DEBIAN"

# program files (no tests, no build clutter)
cp -r "$TOP/p80diag" "$LIB/"
cp "$TOP/run_p80diag.py" "$LIB/"
find "$LIB" -name __pycache__ -prune -exec rm -rf {} +

# launcher
cat > "$STAGE/usr/bin/p80diag" <<'EOF'
#!/bin/sh
exec python3 /usr/lib/p80-diagnostics/run_p80diag.py "$@"
EOF
chmod 755 "$STAGE/usr/bin/p80diag"

# menu entry and icon
cat > "$STAGE/usr/share/applications/$PKG.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=P80 Diagnostics
Comment=Read and clear fault codes on 1996-98 Volvo cars (K-line)
Exec=p80diag
Icon=$PKG
Terminal=false
Categories=Utility;Development;
Keywords=Volvo;OBD;K-line;diagnostics;DTC;
EOF
ICON="$STAGE/usr/share/icons/hicolor/256x256/apps/$PKG.png"
if command -v convert >/dev/null 2>&1; then
    convert "$TOP/p80diag/icon.png" -resize 256x256 "$ICON"
else
    cp "$TOP/p80diag/icon.png" "$ICON"
fi

# docs
cp "$TOP/README.md" "$STAGE/usr/share/doc/$PKG/"
{
    echo "P80 Diagnostics"
    echo "Copyright (C) 2026 Rick Hale Parker"
    echo "License: GPL-3.0-or-later (see /usr/share/common-licenses/GPL-3)"
} > "$STAGE/usr/share/doc/$PKG/copyright"

# package metadata
SIZE=$(du -sk "$STAGE/usr" | cut -f1)
cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.8), python3-tk, python3-serial
Installed-Size: $SIZE
Maintainer: Rick Hale Parker <RickParker22@gmail.com>
Description: Fault code reader for 1996-98 Volvo cars over K-line
 Reads and clears fault codes, reads ECU IDs and resets the service light
 on 850, S/V70, C70, 900/S/V90 and S/V40 cars. Works with a K-line (KKL)
 cable or an ELM327 / OBDLink adapter.
 .
 An independent, open-source enthusiast project. Not affiliated with,
 authorized, or endorsed by Volvo Car Corporation.
EOF

cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
exit 0
EOF
cat > "$STAGE/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
# only on remove/purge, never during an upgrade (the new files are already in place)
case "$1" in
    remove|purge) rm -rf /usr/lib/p80-diagnostics ;;
esac
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
exit 0
EOF
chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"

# sane permissions (mktemp makes the top folder 700)
find "$STAGE" -type d -exec chmod 755 {} +
find "$STAGE/usr" -type f -exec chmod 644 {} +
chmod 755 "$STAGE/usr/bin/p80diag" "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"

mkdir -p "$TOP/dist"
OUT="$TOP/dist/${PKG}_${VERSION}_all.deb"
dpkg-deb --root-owner-group --build "$STAGE" "$OUT" >/dev/null
echo "Built $OUT"
