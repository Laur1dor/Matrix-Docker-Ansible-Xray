import json
import urllib.parse
from maubot import Plugin
from maubot.handlers import event
from mautrix.types import (EventType, ReactionEvent, MessageEvent, MessageType,
                           Format, TextMessageEventContent, RelatesTo, RelationType, EventID)
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper

FLAG2LANG = {
    "🇬🇧": "en", "🇺🇸": "en", "🇷🇺": "ru", "🇺🇦": "uk", "🇩🇪": "de", "🇫🇷": "fr",
    "🇪🇸": "es", "🇮🇹": "it", "🇵🇹": "pt", "🇧🇷": "pt", "🇵🇱": "pl", "🇹🇷": "tr",
    "🇨🇳": "zh-CN", "🇯🇵": "ja", "🇰🇷": "ko", "🇸🇦": "ar", "🇮🇳": "hi", "🇳🇱": "nl",
    "🇸🇪": "sv", "🇫🇮": "fi", "🇨🇿": "cs", "🇬🇷": "el", "🇮🇱": "he", "🇻🇳": "vi",
    "🇹🇭": "th", "🇮🇩": "id", "🇷🇴": "ro", "🇭🇺": "hu", "🇰🇿": "kk", "🇬🇪": "ka",
    "🇦🇲": "hy", "🇦🇿": "az", "🇷🇸": "sr", "🇧🇬": "bg", "🇩🇰": "da", "🇳🇴": "no",
}
NAME2LANG = {
    "английский": "en", "англ": "en", "english": "en", "en": "en",
    "русский": "ru", "рус": "ru", "russian": "ru", "ru": "ru",
    "немецкий": "de", "de": "de", "deutsch": "de", "german": "de",
    "французский": "fr", "fr": "fr", "french": "fr",
    "испанский": "es", "es": "es", "spanish": "es", "español": "es",
    "итальянский": "it", "it": "it", "китайский": "zh-CN", "zh": "zh-CN",
    "японский": "ja", "ja": "ja", "корейский": "ko", "ko": "ko",
    "украинский": "uk", "uk": "uk", "польский": "pl", "pl": "pl",
    "турецкий": "tr", "tr": "tr", "арабский": "ar", "ar": "ar",
    "португальский": "pt", "pt": "pt", "нидерландский": "nl", "nl": "nl",
}


class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("ask_emoji")


class ReactTransBot(Plugin):
    async def start(self) -> None:
        self.config.load_and_update()
        self.pending = {}  # our question event_id -> {"text":..., "asker":...}

    @classmethod
    def get_config_class(cls):
        return Config

    async def _gtranslate(self, text: str, tl: str) -> str:
        url = ("https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=%s&dt=t&q=%s"
               % (tl, urllib.parse.quote(text)))
        async with self.http.get(url) as r:
            data = await r.json(content_type=None)
        return "".join(seg[0] for seg in data[0] if seg and seg[0])

    async def _resolve_lang(self, ans: str) -> str:
        ans = (ans or "").strip().lower().lstrip("!/.")
        if ans in NAME2LANG:
            return NAME2LANG[ans]
        if 2 <= len(ans) <= 5 and all(c.isalpha() or c == "-" for c in ans):
            return ans  # looks like a code (en, de, zh-cn)
        # fallback: ask the local LLM gateway for the ISO code (covers ANY language by name)
        try:
            payload = {"messages": [{"role": "user",
                "content": ("Дай ISO 639-1 код языка для названия: \"%s\". "
                            "Ответь ТОЛЬКО кодом (например en, de, zh-CN, sw), без пояснений." % ans)}]}
            async with self.http.post("http://172.21.0.1:8765/v1/chat/completions",
                                      json=payload, timeout=20) as r:
                d = await r.json(content_type=None)
            code = d["choices"][0]["message"]["content"].strip().strip(".`\" \n").split()[0]
            if 2 <= len(code) <= 6 and all(c.isalpha() or c == "-" for c in code):
                return code
        except Exception:
            pass
        return ""

    async def _orig_text(self, room_id, event_id):
        try:
            evt = await self.client.get_event(room_id, event_id)
            c = evt.content
            body = getattr(c, "body", None)
            if body and getattr(c, "msgtype", None) in (MessageType.TEXT, MessageType.NOTICE, MessageType.EMOTE):
                return body
        except Exception:
            pass
        return None

    @event.on(EventType.REACTION)
    async def on_reaction(self, evt: ReactionEvent) -> None:
        if evt.sender == self.client.mxid:
            return
        rel = evt.content.relates_to
        if not rel or not rel.event_id:
            return
        key = (rel.key or "").strip()
        target = rel.event_id
        # Variant B: globe -> ask language
        if key == self.config["ask_emoji"]:
            text = await self._orig_text(evt.room_id, target)
            if not text:
                return
            q = TextMessageEventContent(msgtype=MessageType.NOTICE,
                body="На какой язык перевести? Ответь на это сообщение (например: en, ru, немецкий).")
            qid = await self.client.send_message(evt.room_id, q)
            self.pending[str(qid)] = {"text": text, "asker": str(evt.sender)}
            return
        # Variant A: flag -> translate to that language
        lang = FLAG2LANG.get(key)
        if not lang:
            return
        text = await self._orig_text(evt.room_id, target)
        if not text:
            return
        try:
            tr = await self._gtranslate(text, lang)
        except Exception:
            return
        out = TextMessageEventContent(msgtype=MessageType.NOTICE, body=tr,
            relates_to=RelatesTo(rel_type=RelationType("m.in_reply_to"), event_id=EventID(target)))
        # use a plain reply via relates_to m.in_reply_to
        content = TextMessageEventContent(msgtype=MessageType.NOTICE, body=tr)
        content.set_reply(EventID(target))
        await self.client.send_message(evt.room_id, content)

    @event.on(EventType.ROOM_MESSAGE)
    async def on_message(self, evt: MessageEvent) -> None:
        if evt.sender == self.client.mxid:
            return
        rel = evt.content.relates_to
        if not rel:
            return
        reply_to = None
        try:
            reply_to = rel.in_reply_to.event_id if rel.in_reply_to else None
        except Exception:
            reply_to = None
        if not reply_to or str(reply_to) not in self.pending:
            return
        info = self.pending.pop(str(reply_to))
        if str(evt.sender) != info["asker"]:
            self.pending[str(reply_to)] = info
            return
        ans = (evt.content.body or "").strip()
        lang = await self._resolve_lang(ans)
        if not lang:
            await evt.reply("Не понял язык 😕 Попробуй ещё раз через 🌐 (можно любым языком: «английский», «en», «суахили»…).")
            self.pending[str(reply_to)] = info
            return
        try:
            tr = await self._gtranslate(info["text"], lang)
        except Exception:
            await evt.reply("Перевод не удался 😕")
            return
        await evt.reply(tr)
