import unittest

from sovits_tts import payload


class SovitsPayloadTests(unittest.TestCase):
    def test_payload_fields(self):
        body = payload("你好")
        self.assertEqual(body["text"], "你好")
        self.assertEqual(body["text_lang"], "zh")
        self.assertFalse(body["streaming_mode"])
        self.assertIn("ref_audio_path", body)


if __name__ == "__main__":
    unittest.main()
