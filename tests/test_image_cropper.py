import unittest

from utils.image_cropper import ImageCropSettings, _score_qr_candidate


class TestImageCropper(unittest.TestCase):
    def test_detects_low_contrast_colored_qr_pattern(self):
        # A gold QR code can have luminance values above the fixed dark
        # threshold while still containing enough local contrast to detect it.
        size = 88
        pixels = []
        for y in range(size):
            row = []
            for x in range(size):
                module = ((x // 4) + (y // 4)) % 2
                row.append(130 if module else 220)
            pixels.append(row)

        class PixelMatrix:
            def __getitem__(self, point):
                x, y = point
                return pixels[y][x]

        result = _score_qr_candidate(
            PixelMatrix(),
            0,
            0,
            size - 1,
            size - 1,
            ImageCropSettings(),
        )

        self.assertIsNotNone(result)
        self.assertGreaterEqual(result["dark_ratio"], 0.10)
        self.assertGreaterEqual(result["transition_density"], 0.10)


if __name__ == "__main__":
    unittest.main()
