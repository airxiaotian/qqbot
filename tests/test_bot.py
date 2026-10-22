from qqbot import Bot, Reply, default_reply


def test_default_reply_uses_oneesan_style():
    reply = default_reply("hello")
    assert "hello" in reply
    assert "oneesan" in reply.lower()


def test_reply_object_is_returned():
    bot = Bot()
    response = bot.reply("hello", user="alice")
    assert isinstance(response, Reply)
    assert response.user == "alice"
    assert response.sender == "oneesan"
    assert response.text.startswith("はいはい")


def test_bot_aliases_work():
    bot = Bot()
    assert str(bot.send("ping")) == str(bot.receive("ping"))
    assert bot("test").content == bot.respond("test").content
