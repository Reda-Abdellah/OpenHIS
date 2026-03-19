import io
import logging

import numpy as np
import pydicom
import skimage.transform
import torch
import torchxrayvision as xrv

logger = logging.getLogger("ai-service.inference")

MODEL_NAME = "densenet121-res224-all"

logger.info("Loading TorchXRayVision model: %s …", MODEL_NAME)
_model = xrv.models.DenseNet(weights=MODEL_NAME)
_model.eval()
logger.info("Model loaded — pathologies: %s", _model.pathologies)


def run_inference(dicom_bytes: bytes) -> dict:
    ds = pydicom.dcmread(io.BytesIO(dicom_bytes))

    pixel_array = ds.pixel_array.astype(np.float32)

    # Multi-frame safety: use first frame
    if pixel_array.ndim == 3:
        pixel_array = pixel_array[0]

    if pixel_array.ndim != 2:
        raise ValueError(f"Unsupported pixel array shape: {pixel_array.shape}")

    # Normalise to [-1024, 1024] as required by TorchXRayVision
    p_min, p_max = pixel_array.min(), pixel_array.max()
    if p_max - p_min > 0:
        pixel_array = (pixel_array - p_min) / (p_max - p_min) * 2048.0 - 1024.0
    else:
        pixel_array = np.zeros_like(pixel_array)

    # Resize to 224×224
    img = skimage.transform.resize(
        pixel_array, (224, 224), anti_aliasing=True, preserve_range=True
    ).astype(np.float32)

    # Shape: (batch=1, channels=1, H=224, W=224)
    tensor = torch.from_numpy(img).unsqueeze(0).unsqueeze(0)

    with torch.no_grad():
        outputs = _model(tensor)

    probs          = outputs[0].detach().numpy()
    pathology_names = _model.pathologies

    predictions = {
        name: round(float(prob), 4)
        for name, prob in zip(pathology_names, probs)
    }

    sorted_preds = sorted(predictions.items(), key=lambda x: x[1], reverse=True)
    top3 = [{"pathology": k, "probability": v} for k, v in sorted_preds[:3]]

    return {"predictions": predictions, "top3": top3}
