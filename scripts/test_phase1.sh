#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SAMPLE="$SCRIPT_DIR/sample.dcm"

echo "======================================================"
echo " PACS Demo — Phase 1: End-to-End AI Pipeline Test"
echo "======================================================"
echo ""

# Download CR sample if missing
if [ ! -f "$SAMPLE" ]; then
  echo "[*] Downloading public CR DICOM sample …"
  curl -fsSL \
    "https://github.com/pydicom/pydicom/raw/main/pydicom/data/test_files/CR_small.dcm" \
    -o "$SAMPLE" || {
      echo "[!] Please place a CR or DX DICOM file at: $SAMPLE"
      exit 1
    }
fi

echo "[*] Uploading $SAMPLE to Orthanc …"
RESPONSE=$(curl -s -X POST \
  http://localhost/orthanc/instances \
  --data-binary @"$SAMPLE" \
  -H "Content-Type: application/dicom")

echo "[+] Upload response: $RESPONSE"

INSTANCE_ID=$(echo "$RESPONSE" | python3 -c \
  "import sys, json; d=json.load(sys.stdin); print(d.get('ID',''))" 2>/dev/null)

if [ -z "$INSTANCE_ID" ] || [ "$INSTANCE_ID" = "null" ]; then
  echo "[!] Could not extract instance ID from upload response."
  exit 1
fi
echo "[+] Instance ID: $INSTANCE_ID"

echo ""
echo "[*] Waiting 10 seconds for Orthanc plugin to process the image …"
sleep 10

echo ""
echo "[*] Fetching AI metadata (slot 9999) …"
META=$(curl -s "http://localhost/orthanc/instances/${INSTANCE_ID}/metadata/9999")

if [ -z "$META" ]; then
  echo "[!] No metadata found. Check Orthanc plugin logs:"
  echo "    docker compose logs orthanc | grep AI-Plugin"
  exit 1
fi

echo "[+] Raw metadata:"
echo "$META"

echo ""
echo "[*] Pretty-printed predictions:"
echo "$META" | python3 -c "
import sys, json
data = json.load(sys.stdin)
top3 = data.get('top3', [])
print('\nTOP-3 FINDINGS:')
for i, item in enumerate(top3, 1):
    print(f'  {i}. {item[\"pathology\"]:<30} {item[\"probability\"]*100:.2f}%')
print('\nALL PATHOLOGIES (sorted by confidence):')
preds = sorted(data.get('predictions', {}).items(), key=lambda x: x[1], reverse=True)
for name, prob in preds:
    bar = '█' * int(prob * 30)
    print(f'  {name:<35} {prob*100:5.2f}%  {bar}')
"

echo ""
echo "======================================================"
echo " AI Panel URL:"
echo "   http://localhost/ai-panel.html?instance_id=${INSTANCE_ID}"
echo ""
echo " OHIF Viewer:"
echo "   http://localhost/"
echo "======================================================"
