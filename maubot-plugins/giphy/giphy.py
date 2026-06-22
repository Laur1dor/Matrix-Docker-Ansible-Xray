import random
import urllib.parse
from maubot import Plugin, MessageEvent
from maubot.handlers import command
from mautrix.types import ImageInfo, MessageType
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper


class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("api_key")
        helper.copy("rating")
        helper.copy("lang")


class GiphyBot(Plugin):
    async def start(self) -> None:
        self.config.load_and_update()

    @classmethod
    def get_config_class(cls):
        return Config

    @command.new("giphy", aliases=["gif"], help="!giphy <запрос> — прислать гифку")
    @command.argument("query", pass_raw=True, required=True)
    async def giphy(self, evt: MessageEvent, query: str) -> None:
        query = (query or "").strip()
        if not query:
            await evt.reply("Использование: !giphy <запрос>")
            return
        key = self.config["api_key"]
        api = ("https://api.giphy.com/v1/gifs/search?api_key=%s&q=%s&limit=25&rating=%s&lang=%s"
               % (key, urllib.parse.quote(query), self.config["rating"], self.config["lang"]))
        try:
            async with self.http.get(api) as r:
                data = await r.json()
        except Exception:
            await evt.reply("Giphy сейчас недоступен 😕")
            return
        gifs = data.get("data", [])
        if not gifs:
            await evt.reply("По запросу «%s» ничего не нашёл 😕" % query)
            return
        g = random.choice(gifs)
        imgs = g.get("images", {})
        pick = imgs.get("downsized_medium") or imgs.get("original") or {}
        gif_url = pick.get("url")
        if not gif_url:
            await evt.reply("Не смог получить гифку 😕")
            return
        try:
            async with self.http.get(gif_url) as r:
                blob = await r.read()
        except Exception:
            await evt.reply("Не смог скачать гифку 😕")
            return
        fn = "%s.gif" % query[:32]
        mxc = await self.client.upload_media(blob, mime_type="image/gif", filename=fn)
        info = ImageInfo(mimetype="image/gif", size=len(blob),
                         width=int(pick.get("width") or 0), height=int(pick.get("height") or 0))
        await self.client.send_image(evt.room_id, url=mxc, file_name=fn, info=info)
