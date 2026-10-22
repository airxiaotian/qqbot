import unittest
from unittest.mock import MagicMock, patch

from perception import describe_image, transcribe_wav


class PerceptionTests(unittest.TestCase):
    @patch("perception.get_openai_client")
    def test_transcribe_without_audio_api(self, get_client):
        client = MagicMock()
        del client.audio
        get_client.return_value = client
        text = transcribe_wav(b"1234")
        self.assertIn("未配置", text)

    @patch("perception.chat_completions_create")
    def test_describe_image_calls_vision(self, create):
        choice = MagicMock()
        choice.message.content = "一只猫"
        create.return_value.choices = [choice]
        out = describe_image("abcd")
        self.assertEqual(out, "一只猫")
        self.assertTrue(create.called)


if __name__ == "__main__":
    unittest.main()
