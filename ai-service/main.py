import io
import logging

import pydicom
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from inference import MODEL_NAME, run_inference

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai-service")

app = FastAPI(title="PACS AI Service", version="1.0.0")


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME}


@app.post("/predict")
async def predict(file: UploadFile = File(...)):
    contents = await file.read()

    # Validate DICOM
    try:
        pydicom.dcmread(io.BytesIO(contents))
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid DICOM file — could not parse: {exc}",
        )

    try:
        result = run_inference(contents)
    except Exception as exc:
        logger.exception("Inference failed")
        raise HTTPException(status_code=500, detail=f"Inference failed: {exc}")

    logger.info("Inference OK — top pathology: %s", result["top3"][0] if result["top3"] else "n/a")
    return JSONResponse(content=result)
