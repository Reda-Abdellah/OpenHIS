#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SAMPLE="$SCRIPT_DIR/sample.dcm"

echo "======================================================"
echo " PACS Demo — Phase 0: Upload & Connectivity Test"
echo "======================================================"
echo ""

# Download a public sample DICOM (CR modality from pydicom test suite)
if [ ! -f "$SAMPLE" ]; then
  echo "[*] Downloading public sample CR DICOM …"
  curl -fsSL \
    "https://github.com/pydicom/pydicom/blob/main/src/pydicom/data/test_files/CT_small.dcm" \
    -o "$SAMPLE" || {
      echo ""
      echo "[!] Auto-download failed. Please place a .dcm file at:"
      echo "    $SAMPLE"
      exit 1
    }
  echo "[+] Downloaded: $SAMPLE"
else
  echo "[*] Using existing sample: $SAMPLE"
fi

echo ""
echo "[*] Uploading DICOM to Orthanc …"
RESPONSE=$(curl -s -X POST \
  http://localhost/orthanc/instances \
  --data-binary @"$SAMPLE" \
  -H "Content-Type: application/dicom")

echo "[+] Upload response: $RESPONSE"

INSTANCE_ID=$(echo "$RESPONSE" | python3 -c \
  "import sys, json; d=json.load(sys.stdin); print(d.get('ID','UNKNOWN'))" 2>/dev/null || echo "UNKNOWN")
echo "[+] Instance ID: $INSTANCE_ID"

echo ""
echo "[*] Querying studies …"
STUDIES=$(curl -s http://localhost/orthanc/studies)
NUM=$(echo "$STUDIES" | python3 -c "import sys, json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "?")
echo "[+] Studies in Orthanc: $NUM"

echo ""
echo "[*] Checking AI service health …"
curl -s http://localhost/ai/health | python3 -m json.tool || true

echo ""
echo "======================================================"
echo " Open the OHIF viewer at:  http://localhost/"
echo " Orthanc REST API at:      http://localhost/orthanc/app/explorer.html"
echo "======================================================"
