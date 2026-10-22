import unittest
from unittest.mock import MagicMock

from perception import describe_image, transcribe_wav


class PerceptionTests(unittest.TestCase):
    def test_transcribe_without_audio_api(self):
        client = MagicMock()
        del client.audio
        text = transcribe_wav(b"1234", lambda _: client)
        self.assertIn("未配置", text)

    def test_describe_image_calls_vision(self):
        client = MagicMock()
        choice = MagicMock()
        choice.message.content = "一只猫"
        client.chat.completions.create.return_value.choices = [choice]
        out = describe_image("abcd", lambda _: client)
        self.assertEqual(out, "一只猫")
        self.assertTrue(client.chat.completions.create.called)


if __name__ == "__main__":
    unittest.main()
