#!/usr/bin/env bash
set -euo pipefail
WEQROM_PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
WEQROM_TOOLS_DIR="${WEQROM_TOOLS_DIR:-/workspace/.chits-tools}"
WEQROM_SIGNING_DIR="${WEQROM_SIGNING_DIR:-/workspace/.weqrom-ai-signing}"
WEQROM_SDK="$WEQROM_TOOLS_DIR/build-tools/android-15"
WEQROM_ANDROID_JAR="$WEQROM_TOOLS_DIR/platform/android-35/android.jar"
cd "$WEQROM_PROJECT_DIR"
# A release without actual embedded weights is not a ready offline assistant.
python3 - <<'PY'
from pathlib import Path
import hashlib,json
p=Path('app/src/main/assets/model');m=json.loads((p/'manifest.json').read_text())
b=(p/'weqrom-seed-f16.gguf').read_bytes()
assert b[:4]==b'GGUF' and len(b)==m['bytes'] and hashlib.sha256(b).hexdigest()==m['sha256']
assert m['trained_from_scratch'] is True and m['parameters']>0
assert Path('app/src/main/assets/index.html').is_file(), 'Frontend assets are missing'
PY
bash native/build.sh
mkdir -p build/classes build/dex dist
python3 - <<'PY'
from pathlib import Path
for p in Path('build/classes').rglob('*.class'):p.unlink()
for p in Path('build/dex').glob('*.dex'):p.unlink()
Path('build/sources.txt').write_text('\n'.join(str(p) for p in Path('app/src/main/java').rglob('*.java')))
PY
java -jar "$WEQROM_TOOLS_DIR/ecj.jar" -1.8 -proc:none -warn:none -classpath "$WEQROM_ANDROID_JAR" -d build/classes @build/sources.txt
python3 - <<'PY'
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
with ZipFile('build/classes.jar','w',ZIP_DEFLATED) as z:
 for p in sorted(Path('build/classes').rglob('*.class')):z.write(p,p.relative_to('build/classes').as_posix())
PY
"$WEQROM_SDK/d8" --min-api 26 --lib "$WEQROM_ANDROID_JAR" --output build/dex build/classes.jar
"$WEQROM_SDK/aapt" package -f -0 gguf -M app/src/main/AndroidManifest.xml -S app/src/main/res -A app/src/main/assets -I "$WEQROM_ANDROID_JAR" -F build/base.apk
python3 - <<'PY'
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
with ZipFile('build/base.apk','a',ZIP_DEFLATED) as z:
 for p in sorted(Path('build/dex').glob('*.dex')):z.write(p,p.name)
 for p in sorted(Path('app/src/main/jniLibs').rglob('*.so')):z.write(p,'lib/'+p.relative_to('app/src/main/jniLibs').as_posix())
PY
"$WEQROM_SDK/zipalign" -P 16 -f 4 build/base.apk build/aligned.apk
mkdir -p "$WEQROM_SIGNING_DIR"
if [[ ! -f "$WEQROM_SIGNING_DIR/release.p12" ]]; then
  umask 077
  openssl rand -base64 32 > "$WEQROM_SIGNING_DIR/password"
  keytool -genkeypair -keystore "$WEQROM_SIGNING_DIR/release.p12" -storetype PKCS12 -alias weqrom-ai \
    -storepass:file "$WEQROM_SIGNING_DIR/password" -keypass:file "$WEQROM_SIGNING_DIR/password" \
    -keyalg RSA -keysize 3072 -validity 10000 -dname 'CN=Weqrom AI, O=Weqrom' -noprompt
fi
"$WEQROM_SDK/apksigner" sign --ks "$WEQROM_SIGNING_DIR/release.p12" --ks-key-alias weqrom-ai \
  --ks-pass "file:$WEQROM_SIGNING_DIR/password" --out dist/Weqrom-AI.apk build/aligned.apk
"$WEQROM_SDK/apksigner" verify --verbose --print-certs dist/Weqrom-AI.apk > dist/signature-report.txt
"$WEQROM_SDK/zipalign" -c -P 16 4 dist/Weqrom-AI.apk
(cd dist && sha256sum Weqrom-AI.apk > SHA256SUMS.txt)
"$WEQROM_SDK/aapt" dump badging dist/Weqrom-AI.apk > dist/badging.txt
printf '%s\n' 'Built dist/Weqrom-AI.apk with verified embedded model assets.'
