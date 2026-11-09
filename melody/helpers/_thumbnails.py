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
        self.size = (1280, 720)
        self.fill = (255, 255, 255)
        # Font အရွယ်အစားများ
        self.font_n = ImageFont.truetype("melody/helpers/Raleway-Bold.ttf", 22)  # Channel
        self.font_t = ImageFont.truetype("melody/helpers/Raleway-Bold.ttf", 34)  # Title
        self.font_c = ImageFont.truetype("melody/helpers/Inter-Light.ttf", 20)   # Logo / Extra text
        self.font_d = ImageFont.truetype("melody/helpers/Inter-Light.ttf", 20)   # Duration
        
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

    async def generate(self, song: Track, size=(1280, 720)) -> str:
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

    async def _generate(self, song: Track, size=(1280, 720)) -> str:
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
            
            # 1. နောက်ခံကို မျက်နှာပြင်အပြည့် Blur လုပ်ခြင်း
            image = ImageOps.fit(
                source,
                size,
                method=Image.Resampling.BILINEAR,
                centering=(0.5, 0.5),
            ).filter(ImageFilter.GaussianBlur(25))
            
            image = ImageEnhance.Brightness(image).enhance(0.5)

            # 2. controls.png ကို မျက်နှာပြင်အပြည့် (1280x720) ဖြင့် paste လုပ်ခြင်း
            try:
                controls = Image.open("melody/helpers/controls.png").convert("RGBA")
                controls_resized = controls.resize(size, Image.Resampling.LANCZOS)
                image.paste(controls_resized, (0, 0), controls_resized)
            except Exception as e:
                logger.error(f"Controls overlay error: {e}")

            # 3. 🟢 YouTube Thumbnail ပုံငယ်ကို ပိုကြီးအောင် ချိန်ညှိခြင်း (Size: 180x180)
            thumb_size = 180
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
            ImageDraw.Draw(thumb_mask).rounded_rectangle((0, 0, thumb_size, thumb_size), radius=25, fill=255)

            rounded = ImageOps.fit(resize, (thumb_size, thumb_size))
            rounded.putalpha(thumb_mask)

            # Thumbnail paste မည့်နေရာ (X=230, Y=95)
            image.paste(rounded, (230, 95), rounded)

        # စာသားများ သတ်မှတ်ခြင်း
        channel = (song.channel_name or "KHH")[:22]
        title = (song.title or "CORES")[:25]
        duration = song.duration or "3:54"

        draw = ImageDraw.Draw(image)
        
        # 4. 🟢 ညာဘက်ခြမ်းတွင် Channel, Title နှင့် Logo စာသားများကို နေရာတကျ ဖော်ပြခြင်း
        draw.text((435, 105), channel, (180, 180, 180), font=self.font_n)
        draw.text((435, 140), title, (255, 255, 255), font=self.font_t)
        draw.text((437, 190), "Telegram Music Bot", (200, 200, 200), font=self.font_c) # ကိုယ့် Logo စာသား
        
        # 5. 🟢 Progress Bar ရဲ့ ဘယ်ညာ အစွန်ဆုံးတွင် အချိန်များ ထည့်သွင်းခြင်း
        draw.text((230, 295), "0:24", (200, 200, 200), font=self.font_d)      # ဘယ်ဘက်အစွန်း အချိန်
        draw.text((1010, 295), duration, (200, 200, 200), font=self.font_d)   # ညာဘက်အစွန်း အချိန် (Duration)

        temp = output.with_name(f"{output.stem}.tmp{output.suffix}")
        image.convert("RGB").save(
            temp,
            format="JPEG",
            quality=90,
            subsampling=2,
        )
        temp.replace(output)
