from qqbot import Bot, Reply, default_reply


def test_default_reply_uses_oneesan_style():
    reply = default_reply("hello")
    assert "hello" in reply
    assert "oneesan" in reply.lower()


def test_default_reply_handles_special_cases():
    assert default_reply("") == "はい、oneesanがあなたの話を聞いていますよ〜"
    assert default_reply("hello?") == "ええ、helloってことですね。oneesanがしっかり応援します〜"


def test_reply_object_is_returned():
    bot = Bot()
    response = bot.reply("hello", user="alice")
    assert isinstance(response, Reply)
    assert response.user == "alice"
    assert response.sender == "oneesan"
    assert response.text.startswith("はいはい")


def test_bot_aliases_work():
    bot = Bot()
    same_input = "ping"
    send_response = bot.send(same_input)
    receive_response = bot.receive(same_input)
    call_response = bot(same_input)
    respond_response = bot.respond(same_input)

    assert send_response == receive_response == call_response == respond_response
    assert send_response.sender == bot.name
    assert call_response.user is None
