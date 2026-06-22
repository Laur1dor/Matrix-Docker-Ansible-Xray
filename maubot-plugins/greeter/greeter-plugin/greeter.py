from maubot import Plugin
from maubot.handlers import event
from mautrix.types import (EventType, StateEvent, Membership, Format, MessageType,
                           TextMessageEventContent, RoomID, UserID)
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper


class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("announce_room")
        helper.copy("homeserver_suffix")
        helper.copy("skip_users")
        helper.copy("welcome_html")
        helper.copy("welcome_plain")


class GreeterBot(Plugin):
    async def start(self) -> None:
        self.config.load_and_update()
        self.pending = {}   # dm_room_id -> user_id (awaiting the user's join)
        self.greeted = set()

    @classmethod
    def get_config_class(cls):
        return Config

    @event.on(EventType.ROOM_MEMBER)
    async def on_member(self, evt: StateEvent) -> None:
        if evt.content.membership != Membership.JOIN:
            return
        user = str(evt.state_key)
        room = str(evt.room_id)

        # case 1: a NEW user joined the announcements room -> open a DM for them
        if room == self.config["announce_room"]:
            if user == str(self.client.mxid) or user in self.config["skip_users"]:
                return
            if not user.endswith(self.config["homeserver_suffix"]):
                return
            prev = evt.unsigned.prev_content if evt.unsigned else None
            if prev and prev.membership == Membership.JOIN:
                return
            if user in self.greeted:
                return
            self.greeted.add(user)   # mark BEFORE any await to avoid a double-DM race
            try:
                dm = await self.client.create_room(invitees=[UserID(user)], is_direct=True)
                self.pending[str(dm)] = user
                self.log.info(f"greeter: opened DM {dm} for new user {user}")
            except Exception as e:
                self.log.warning(f"greeter: failed to open DM for {user}: {e}")
            return

        # case 2: the invited user joined the DM we created -> send the welcome now (so it decrypts)
        if room in self.pending and user == self.pending[room]:
            del self.pending[room]
            content = TextMessageEventContent(
                msgtype=MessageType.TEXT, format=Format.HTML,
                body=self.config["welcome_plain"], formatted_body=self.config["welcome_html"])
            try:
                await self.client.send_message(RoomID(room), content)
                self.log.info(f"greeter: welcomed {user} in {room}")
            except Exception as e:
                self.log.warning(f"greeter: failed to welcome {user}: {e}")
