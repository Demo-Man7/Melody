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
        self.font1 = ImageFont.truetype("melody/helpers/Raleway-Bold.ttf", 30)
        self.font2 = ImageFont.truetype("melody/helpers/Inter-Light.ttf", 30)
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
        """သီချင်း၏ Thumbnail ကို iOS Player ကတ်ပြားပုံစံဖြင့် တိုက်ရိုက်ထုတ်ယူရန်"""
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
        for suffix in ("jpg", "png"):  # 👈 type hint ဖြုတ်ပြီးသား
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
            
            # နောက်ခံကို ကြီးချဲ့၍ Blur လုပ်ခြင်း
            background = ImageOps.fit(
                source,
                size,
                method=Image.Resampling.BILINEAR,
                centering=(0.5, 0.5),
            )
            image = ImageEnhance.Brightness(
                background.filter(ImageFilter.GaussianBlur(20))
            ).enhance(0.45)

            # အလယ်ကတ်ပြား ဖန်တီးရန် (Box)
            box = (183, 30, 1097, 544)
            region = image.crop(box)

            mask = Image.new("L", region.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, region.size[0], region.size[1]),
                radius=25,
                fill=255,
            )
            image.paste(region, box, mask)

            # 🟢 controls.png ဖိုင်ကို ကတ်ပြားပေါ်သို့ ပေါင်းထည့်ခြင်း
            try:
                controls = Image.open("melody/helpers/controls.png").convert("RGBA")
                image.paste(controls, (183, 30), controls)
            except Exception:
                pass

            # သီချင်း Thumbnail ပုံကို ကတ်ပြားအတွင်း ဘယ်ဘက်ခြမ်းတွင် ထည့်ရန်
            thumb_size = (160, 160)
            thumb_img = ImageOps.fit(source, thumb_size, method=Image.Resampling.LANCZOS)
            thumb_mask = Image.new("L", thumb_size, 0)
            ImageDraw.Draw(thumb_mask).rounded_rectangle((0, 0, thumb_size[0], thumb_size[1]), radius=15, fill=255)
            thumb_img.putalpha(thumb_mask)
            image.paste(thumb_img, (220, 65), thumb_img)

        channel = (song.channel_name or "")[:25]
        title = (song.title or "")[:40]
        duration = song.duration or "00:00"

        draw = ImageDraw.Draw(image)
        draw.text(
            xy=(400, 75),
            text=channel,
            font=self.font2,
            fill=(200, 200, 200),
        )
        draw.text((400, 110), title, font=self.font1, fill=self.fill)
        draw.text((220, 245), "0:01", font=self.font2, fill=(200, 200, 200))
        draw.line([(290, 255), (1000, 255)], fill=(150, 150, 150), width=4)
        draw.text((1020, 245), duration, font=self.font2, fill=(200, 200, 200))

        temp = output.with_name(f"{output.stem}.tmp{output.suffix}")
        image.convert("RGB").save(
            temp,
            format="JPEG",
            quality=90,
            subsampling=2,
        )
        temp.replace(output)
