# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of Melody


import asyncio
from contextlib import suppress
from io import BytesIO
from pathlib import Path

import aiohttp
from PIL import (Image, ImageDraw, ImageEnhance,
                 ImageFilter, ImageFont, ImageOps)

from melody import config, logger
from melody.helpers import Track


class Thumbnail:
    MAX_DOWNLOAD_BYTES = 5 * 1024**2

    def __init__(self):
        self.size = (640, 640)
        self.fill = (255, 255, 255)
        # cloudymusic ပုံစံ Font အရွယ်အစားများနှင့် ကိုက်ညီစေရန်
        self.font_n = ImageFont.truetype("melody/helpers/Raleway-Bold.ttf", 15)  # Channel / Source
        self.font_t = ImageFont.truetype("melody/helpers/Raleway-Bold.ttf", 20)  # Title
        self.font_c = ImageFont.truetype("melody/helpers/Inter-Light.ttf", 15)   # Artist/Extra
        self.font_d = ImageFont.truetype("melody/helpers/Inter-Light.ttf", 12)   # Duration
        
        self.session: aiohttp.ClientSession | None = None
        self.timeout = aiohttp.ClientTimeout(total=8, connect=3, sock_read=5)
        self.pending: dict[str, asyncio.Task[str]] = {}

    async def start(self) -> None:
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=self.timeout)

    async def close(self) -> None:
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
        self.pending.clear()
        if self.session and not self.session.closed:
            await self.session.close()

    async def generate(self, song: Track, size=(640, 640)) -> str:
        cached = self.cached_path(song.id)
        if cached:
            return cached

        if song.id in self.pending:
            return await self.pending[song.id]

        task = asyncio.create_task(self._generate(song, size))
        self.pending[song.id] = task
        try:
            return await task
        finally:
            self.pending.pop(song.id, None)

    def cached_path(self, song_id: str) -> str | None:
        for suffix in ("jpg", "png"):
            path = Path("cache") / f"{song_id}.{suffix}"
            if path.exists():
                return str(path)
        return None

    async def download_thumb(self, url: str) -> bytes:
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=self.timeout)

        async with self.session.get(url, allow_redirects=False) as resp:
            resp.raise_for_status()
            if (
                resp.content_length is not None
                and resp.content_length > self.MAX_DOWNLOAD_BYTES
            ):
                raise ValueError("Thumbnail response is too large")
            data = bytearray()
            async for chunk in resp.content.iter_chunked(64 * 1024):
                data.extend(chunk)
                if len(data) > self.MAX_DOWNLOAD_BYTES:
                    raise ValueError("Thumbnail response is too large")
            return bytes(data)

    async def _generate(self, song: Track, size=(640, 640)) -> str:
        output = Path("cache") / f"{song.id}.png"
        try:
            if not song.thumbnail:
                return config.DEFAULT_THUMB
            data = await self.download_thumb(song.thumbnail)
            await asyncio.to_thread(self.render, data, output, song, size)
            if output.exists():
                return str(output)
            return config.DEFAULT_THUMB
        except Exception as ex:
            logger.error(
                f"Thumbnail generation failed for {song.id} ({song.thumbnail}): {ex}"
            )
            return config.DEFAULT_THUMB

    def render(
        self,
        data: bytes,
        output: Path,
        song: Track,
        size: tuple[int, int],
    ) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)

        with Image.open(BytesIO(data)) as source:
            source = ImageOps.exif_transpose(source).convert("RGBA")
            
            # 1. နောက်ခံကို Blur လုပ်ခြင်း (cloudymusic ပုံစံအတိုင်း)
            bg = source.filter(ImageFilter.GaussianBlur(25))
            box = (120, 120, 520, 480)

            region = bg.crop(box)
            dark_region = ImageEnhance.Brightness(region).enhance(0.5)

            mask = Image.new("L", dark_region.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, box[2] - box[0], box[3] - box[1]), 40, fill=255
            )

            bg.paste(dark_region, box, mask)

            # 2. controls.png ဖိုင်ကို ပုံစံတူ နေရာချခြင်း
            try:
                controls = Image.open("melody/helpers/controls.png").convert("RGBA")
                bg.paste(controls, (135, 305), controls)
            except Exception:
                pass

            # 3. သီချင်း Thumbnail ပုံငယ်ကို rounded square ပုံစံပြုလုပ်၍ ထည့်ရန်
            thumb_size = 125
            width, height = source.size
            side_length = min(width, height)
            crop = source.crop(
                (
                    (width - side_length) // 2,
                    (height - side_length) // 2,
                    (width + side_length) // 2,
                    (height + side_length) // 2,
                )
            )
            resize = crop.resize((thumb_size, thumb_size), Image.Resampling.LANCZOS)

            thumb_mask = Image.new("L", (thumb_size, thumb_size), 0)
            ImageDraw.Draw(thumb_mask).rounded_rectangle((0, 0, thumb_size, thumb_size), radius=30, fill=255)

            rounded = ImageOps.fit(resize, (thumb_size, thumb_size))
            rounded.putalpha(thumb_mask)

            # ပုံ (3) ပါ ပုံစံအတိုင်း Thumbnail ကို ကတ်ပြားအတွင်း paste လုပ်ရန်
            paste_x, paste_y = 145, 155
            bg.paste(rounded, (paste_x, paste_y), rounded)

        # စာသားများ သတ်မှတ်ခြင်း
        channel = (song.channel_name or "Melody")[:17]
        title = (song.title or "Unknown")[:17]
        duration = song.duration or "0:00"

        draw = ImageDraw.Draw(bg)
        
        # 4. ပုံ (3) ပါ နေရာအတိုင်း စာသားများ ရေးဆွဲခြင်း
        draw.text((285, 180), channel, (192, 192, 192), font=self.font_n)
        draw.text((285, 200), title, (255, 255, 255), font=self.font_t)
        draw.text((287, 235), "Telegram Music", (255, 255, 255), font=self.font_c)
        draw.text((478, 321), duration, (192, 192, 192), font=self.font_d)

        temp = output.with_name(f"{output.stem}.tmp{output.suffix}")
        bg.convert("RGB").save(
            temp,
            format="JPEG",
            quality=90,
            subsampling=2,
        )
        temp.replace(output)
