"""Camera-only preview for inspection and selecting infrared eye regions."""
from .types import Observation


class PreviewTracker:
    """Expose frames without loading a face model or producing gaze coordinates."""

    mode = "preview"
    pupil_polarity = "dark"
    intrinsics = None
    roi_normalized = None

    def context(self, image_size=None):
        value = {"tracking_mode": "preview", "feature_method": "none"}
        if image_size is not None:
            value["image_size"] = list(image_size)
        return value

    def process(self, image_bgr, timestamp_s):
        size = (image_bgr.shape[1], image_bgr.shape[0])
        return Observation(timestamp_s, size, message="preview_only_no_tracking",
                           context=self.context(size))

    def close(self):
        pass
