import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utils.image_cropper import ImageCropSettings, copy_and_crop_image


def build_parser():
    parser = argparse.ArgumentParser(description="本地测试图片裁剪效果")
    parser.add_argument("input", help="输入图片路径")
    parser.add_argument("output", help="输出图片路径")
    parser.add_argument("--top-px", type=int, default=0, help="顶部裁剪像素")
    parser.add_argument("--bottom-px", type=int, default=0, help="底部裁剪像素")
    parser.add_argument("--left-px", type=int, default=0, help="左侧裁剪像素")
    parser.add_argument("--right-px", type=int, default=0, help="右侧裁剪像素")
    parser.add_argument("--top-px-if-qr", type=int, default=None, help="命中二维码后顶部额外裁剪像素")
    parser.add_argument("--top-ratio", type=float, default=None, help="顶部裁剪比例")
    parser.add_argument("--bottom-ratio", type=float, default=None, help="底部裁剪比例")
    parser.add_argument("--left-ratio", type=float, default=None, help="左侧裁剪比例")
    parser.add_argument("--right-ratio", type=float, default=None, help="右侧裁剪比例")
    parser.add_argument("--top-ratio-if-qr", type=float, default=None, help="命中二维码后顶部额外裁剪比例")
    parser.add_argument("--auto-bottom-qr", action="store_true", help="检测到底部二维码区域时自动裁掉底部")
    parser.add_argument("--qr-top-padding-px", type=int, default=8, help="二维码区域上方预留裁切间距")
    parser.add_argument("--preserve-square", action="store_true", help="裁剪后补边保持方图")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    settings = ImageCropSettings(
        enabled=True,
        top_px=args.top_px or None,
        bottom_px=args.bottom_px or None,
        left_px=args.left_px or None,
        right_px=args.right_px or None,
        top_px_if_qr=args.top_px_if_qr,
        top_ratio=args.top_ratio,
        bottom_ratio=args.bottom_ratio,
        left_ratio=args.left_ratio,
        right_ratio=args.right_ratio,
        top_ratio_if_qr=args.top_ratio_if_qr,
        auto_bottom_qr=args.auto_bottom_qr,
        qr_top_padding_px=args.qr_top_padding_px,
        preserve_square=args.preserve_square,
    )

    result = copy_and_crop_image(args.input, args.output, settings)
    print(f"input={Path(args.input)}")
    print(f"output={Path(args.output)}")
    print(f"changed={result['changed']}")
    print(f"original_size={result['original_size']}")
    print(f"cropped_size={result['cropped_size']}")
    print(f"qr_detection={result.get('qr_detection')}")


if __name__ == "__main__":
    main()
