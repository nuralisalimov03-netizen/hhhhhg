#!/usr/bin/env bash
set -euo pipefail
CHITS_PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CHITS_TOOLS_DIR="${CHITS_TOOLS_DIR:-/workspace/.chits-tools}"
CHITS_SIGNING_DIR="${CHITS_SIGNING_DIR:-/workspace/.chits-signing}"
CHITS_ANDROID_JAR="$CHITS_TOOLS_DIR/platform/android-35/android.jar"
CHITS_BUILD_TOOLS="$CHITS_TOOLS_DIR/build-tools/android-15"
cd "$CHITS_PROJECT_DIR"
bash tools/setup-android.sh
if [[ ! -f app/src/main/jniLibs/arm64-v8a/libchits_ai.so ]]; then
  bash ai-native/build.sh
fi
mkdir -p build/classes build/dex dist "$CHITS_SIGNING_DIR"
python3 - <<'PY'
from pathlib import Path
for p in Path('build/classes').rglob('*.class'): p.unlink()
for p in Path('build/dex').glob('*.dex'): p.unlink()
Path('build/sources.txt').write_text('\n'.join(str(p) for p in Path('app/src/main/java').rglob('*.java')))
PY
java -jar "$CHITS_TOOLS_DIR/ecj.jar" -1.8 -proc:none -warn:none -classpath "$CHITS_ANDROID_JAR" -d build/classes @build/sources.txt
python3 - <<'PY'
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
with ZipFile('build/classes.jar','w',ZIP_DEFLATED) as z:
    for p in sorted(Path('build/classes').rglob('*.class')): z.write(p,p.relative_to('build/classes').as_posix())
PY
"$CHITS_BUILD_TOOLS/d8" --min-api 26 --lib "$CHITS_ANDROID_JAR" --output build/dex build/classes.jar
"$CHITS_BUILD_TOOLS/aapt" package -f -M app/src/main/AndroidManifest.xml -S app/src/main/res -A app/src/main/assets -I "$CHITS_ANDROID_JAR" -F build/base.apk
python3 - <<'PY'
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
with ZipFile('build/base.apk','a',ZIP_DEFLATED) as z:
    for p in sorted(Path('build/dex').glob('*.dex')): z.write(p,p.name)
    for p in sorted(Path('app/src/main/jniLibs').rglob('*.so')): z.write(p,'lib/'+p.relative_to('app/src/main/jniLibs').as_posix())
PY
"$CHITS_BUILD_TOOLS/zipalign" -P 16 -f 4 build/base.apk build/aligned.apk
if [[ ! -f "$CHITS_SIGNING_DIR/release.p12" ]]; then
  umask 077
  openssl rand -base64 32 > "$CHITS_SIGNING_DIR/password"
  keytool -genkeypair -keystore "$CHITS_SIGNING_DIR/release.p12" -storetype PKCS12 -alias chits \
    -storepass:file "$CHITS_SIGNING_DIR/password" -keypass:file "$CHITS_SIGNING_DIR/password" \
    -keyalg RSA -keysize 3072 -validity 10000 -dname 'CN=Chits Market, O=Chits, C=RU' -noprompt
fi
"$CHITS_BUILD_TOOLS/apksigner" sign --ks "$CHITS_SIGNING_DIR/release.p12" --ks-key-alias chits \
  --ks-pass "file:$CHITS_SIGNING_DIR/password" --out dist/Chits-Market.apk build/aligned.apk
"$CHITS_BUILD_TOOLS/apksigner" verify --verbose dist/Chits-Market.apk
"$CHITS_BUILD_TOOLS/zipalign" -c -P 16 4 dist/Chits-Market.apk
(cd dist && sha256sum Chits-Market.apk > SHA256SUMS.txt)
printf '%s\n' 'Built dist/Chits-Market.apk; retain the private signing directory for future updates.'
