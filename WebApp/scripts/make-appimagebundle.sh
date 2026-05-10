#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
DIST_DIR="${APP_DIR}/dist"
APPDIR="${DIST_DIR}/AppDir"
RUNTIME="${DIST_DIR}/runtime-x86_64"
APP_NAME="AudioBook-WebApp-Server"
VERSION="1.0.0"
OUTPUT="${DIST_DIR}/${APP_NAME}-${VERSION}-x86_64.AppImage"
LAUNCHER="${DIST_DIR}/${APP_NAME}.sh"
SERVICE="${DIST_DIR}/audiobook-webapp.service"
INSTALL_SCRIPT="${DIST_DIR}/install-daemon.sh"
NODE_VERSION="${NODE_VERSION:-22.22.0}"
NODE_DISTRO="node-v${NODE_VERSION}-linux-x64"
NODE_TARBALL="${DIST_DIR}/${NODE_DISTRO}.tar.xz"
NODE_URL="https://nodejs.org/dist/v${NODE_VERSION}/${NODE_DISTRO}.tar.xz"

echo "==> Preparing AppImage build in ${DIST_DIR}"
# Remove old GUI/Electron artifacts so users do not run the wrong AppImage.
rm -f "${DIST_DIR}/AudioBook WebApp-"*.AppImage
rm -f "${DIST_DIR}/AudioBook-WebApp-"*.AppImage
rm -rf "${DIST_DIR}/linux-unpacked"
rm -f "${DIST_DIR}/builder-debug.yml" "${DIST_DIR}/builder-effective-config.yaml"
rm -rf "${APPDIR}"
mkdir -p "${APPDIR}/usr/bin" "${APPDIR}/usr/lib" "${APPDIR}/app"

# ---- Bundle portable Node.js runtime ----
if [[ ! -f "${NODE_TARBALL}" ]]; then
  echo "==> Downloading portable Node.js runtime (${NODE_DISTRO})"
  curl -fsSL -o "${NODE_TARBALL}" "${NODE_URL}"
fi
echo "==> Extracting portable Node.js runtime"
tar -xJf "${NODE_TARBALL}" -C "${DIST_DIR}"
cp "${DIST_DIR}/${NODE_DISTRO}/bin/node" "${APPDIR}/usr/bin/node"
cp -a "${DIST_DIR}/${NODE_DISTRO}/lib/." "${APPDIR}/usr/lib/"
chmod +x "${APPDIR}/usr/bin/node"
rm -rf "${DIST_DIR}/${NODE_DISTRO}"

echo "==> Pruning glibc core libraries from bundle"
rm -f \
  "${APPDIR}/usr/lib/libc.so"* \
  "${APPDIR}/usr/lib/libm.so"* \
  "${APPDIR}/usr/lib/libdl.so"* \
  "${APPDIR}/usr/lib/libpthread.so"* \
  "${APPDIR}/usr/lib/librt.so"* \
  "${APPDIR}/usr/lib/libresolv.so"* \
  "${APPDIR}/usr/lib/libnss_"* \
  "${APPDIR}/usr/lib/ld-linux"* \
  "${APPDIR}/usr/lib/ld64.so"*

# ---- Copy app source (no dev dirs) ----
echo "==> Copying app files"
rsync -a --exclude node_modules --exclude dist --exclude storage --exclude .git \
  "${APP_DIR}/" "${APPDIR}/app/"

# ---- Install production dependencies inside AppDir ----
echo "==> Installing production npm dependencies"
cd "${APPDIR}/app"
"${APPDIR}/usr/bin/node" "$(command -v npm)" install --omit=dev --prefer-offline 2>/dev/null \
  || npm install --omit=dev
cd "${APP_DIR}"

# ---- AppRun ----
echo "==> Writing AppRun"
cat > "${APPDIR}/AppRun" << 'APPRUN'
#!/usr/bin/env bash
APPDIR="$(dirname "$(readlink -f "$0")")"
# Store books/uploads outside the read-only AppImage bundle
export WEBAPP_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/audiobook-webapp"
# Avoid loading AppImage-bundled glibc artifacts via inherited LD_LIBRARY_PATH.
unset LD_LIBRARY_PATH
export PORT="${PORT:-8090}"
echo "AudioBook WebApp starting on http://0.0.0.0:${PORT}"
echo "Book storage: ${WEBAPP_DATA_DIR}"
exec "${APPDIR}/usr/bin/node" "${APPDIR}/app/server.js" "$@"
APPRUN
chmod +x "${APPDIR}/AppRun"

# ---- .desktop file ----
cat > "${APPDIR}/audiobook-webapp.desktop" << 'DESKTOP'
[Desktop Entry]
Name=AudioBook WebApp
Exec=audiobook-webapp
Icon=audiobook-webapp
Type=Application
Categories=AudioVideo;
DESKTOP

# ---- Placeholder icon (1x1 transparent PNG) ----
printf '\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82' \
  > "${APPDIR}/audiobook-webapp.png"

# ---- Fetch AppImage Type 2 runtime if needed (~20 KB, no FUSE required) ----
if [[ ! -f "${RUNTIME}" ]]; then
  echo "==> Downloading AppImage runtime"
  curl -fsSL -o "${RUNTIME}" \
    "https://github.com/AppImage/AppImageKit/releases/download/continuous/runtime-x86_64"
  chmod +x "${RUNTIME}"
fi

# ---- Build AppImage: mksquashfs + prepend runtime (no FUSE needed) ----
echo "==> Building squashfs"
SQUASHFS="${DIST_DIR}/app.squashfs"
rm -f "${SQUASHFS}"
mksquashfs "${APPDIR}" "${SQUASHFS}" -root-owned -noappend -comp xz -quiet

echo "==> Assembling ${OUTPUT}"
rm -f "${OUTPUT}"
cat "${RUNTIME}" "${SQUASHFS}" > "${OUTPUT}"
chmod +x "${OUTPUT}"
# Mark file as AppImage Type 2
printf '\x41\x49\x02' | dd of="${OUTPUT}" bs=1 seek=8 conv=notrunc 2>/dev/null
rm -f "${SQUASHFS}"

# ---- Write no-FUSE launcher script for headless servers ----
cat > "${LAUNCHER}" << LAUNCH
#!/usr/bin/env bash
set -euo pipefail
DIR="\$(cd -- "\$(dirname -- "\${BASH_SOURCE[0]}")" && pwd)"
export APPIMAGE_EXTRACT_AND_RUN=1
export PORT="\${PORT:-8090}"
exec "\${DIR}/$(basename "${OUTPUT}")" "\$@"
LAUNCH
chmod +x "${LAUNCHER}"

# ---- Write systemd user service ----
cat > "${SERVICE}" << 'SERVICE_FILE'
[Unit]
Description=AudioBook WebApp Server
After=network.target

[Service]
Type=simple
WorkingDirectory=%h
Environment=PORT=8090
Environment=APPIMAGE_EXTRACT_AND_RUN=1
ExecStart=%h/AudioBook-WebApp-Server.sh
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=audiobook-webapp

[Install]
WantedBy=default.target
SERVICE_FILE

# ---- Write daemon install helper ----
APPIMAGE_BASENAME="$(basename "${OUTPUT}")"
LAUNCHER_BASENAME="$(basename "${LAUNCHER}")"
cat > "${INSTALL_SCRIPT}" << INSTALL_EOF
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="\$(cd -- "\$(dirname -- "\${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="audiobook-webapp"
SYSTEMD_USER_DIR="\${HOME}/.config/systemd/user"

for f in ${APPIMAGE_BASENAME} ${LAUNCHER_BASENAME}; do
  if [[ ! -f "\${HOME}/\${f}" ]]; then
    cp "\${SCRIPT_DIR}/\${f}" "\${HOME}/\${f}"
    chmod +x "\${HOME}/\${f}"
    echo "Copied \${f} -> \${HOME}/\${f}"
  fi
done

mkdir -p "\${SYSTEMD_USER_DIR}"
cp "\${SCRIPT_DIR}/\${SERVICE_NAME}.service" "\${SYSTEMD_USER_DIR}/\${SERVICE_NAME}.service"
systemctl --user daemon-reload
systemctl --user enable --now "\${SERVICE_NAME}.service"
echo ""
echo "Service installed and started."
echo "Status:  systemctl --user status \${SERVICE_NAME}"
echo "Logs:    journalctl --user -u \${SERVICE_NAME} -f"
echo "Stop:    systemctl --user stop \${SERVICE_NAME}"
echo "Disable: systemctl --user disable \${SERVICE_NAME}"
INSTALL_EOF
chmod +x "${INSTALL_SCRIPT}"

echo ""
echo "Done: ${OUTPUT}"
echo "Headless server run command:"
echo "  PORT=8090 ${OUTPUT}"
echo "No-FUSE server run command (recommended on Debian servers):"
echo "  PORT=8090 ${LAUNCHER}"
echo "Then open from any browser:"
echo "  http://<server-ip>:8090"
