import unittest

from tactile.frame import TactileFrame
from tactile.protocol import FrameProtocolError, decode_frame, encode_frame


class FrameProtocolTests(unittest.TestCase):
    def test_binary_frame_round_trip(self):
        source = TactileFrame.from_rows(
            [
                [0, 1, 0, 1, 1],
                [1, 0, 0, 0, 1],
            ]
        )
        source.frame_id = 42
        decoded = decode_frame(encode_frame(source))
        self.assertEqual((decoded.cols, decoded.rows), (5, 2))
        self.assertEqual(decoded.pins, source.pins)
        self.assertEqual(decoded.frame_id, 42)

    def test_crc_rejects_corrupted_payload(self):
        source = TactileFrame.from_rows([[0, 1, 0, 1]])
        encoded = bytearray(encode_frame(source))
        encoded[-5] ^= 0x01
        with self.assertRaisesRegex(FrameProtocolError, "CRC32"):
            decode_frame(bytes(encoded))

    def test_uint8_frame_round_trip(self):
        source = TactileFrame.from_rows(
            [[0, 17, 128, 255], [3, 99, 201, 1]],
            levels=256,
            version=2,
        )
        source.frame_id = 77
        decoded = decode_frame(encode_frame(source))
        self.assertEqual(decoded.levels, 256)
        self.assertEqual(decoded.version, 2)
        self.assertEqual(decoded.pins, source.pins)
        self.assertEqual(decoded.frame_id, 77)


if __name__ == "__main__":
    unittest.main()
