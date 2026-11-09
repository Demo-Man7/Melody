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

            # အလယ်ကတ်ပြား ဖန်တီးရန် (Box) - ပုံ (3) ပါ အနေအထားအတိုင်း
            box = (230, 160, 1050, 560)
            region = image.crop(box)

            mask = Image.new("L", region.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, region.size[0], region.size[1]),
                radius=30,
                fill=255,
            )
            image.paste(region, box, mask)

            # controls.png ဖိုင်ကို ကတ်ပြားပေါ်သို့ ပေါင်းထည့်ခြင်း[span_1](start_span)[span_1](end_span)
            try:
                controls = Image.open("melody/helpers/controls.png").convert("RGBA")
                image.paste(controls, (230, 160), controls)
            except Exception:
                pass

            # သီချင်း Thumbnail ပုံငယ်ကို ကတ်ပြားအတွင်း ဘယ်ဘက်ခြမ်းတွင် ထည့်ရန်[span_2](start_span)[span_2](end_span)
            thumb_size = (110, 110)
            thumb_img = ImageOps.fit(source, thumb_size, method=Image.Resampling.LANCZOS)
            thumb_mask = Image.new("L", thumb_size, 0)
            ImageDraw.Draw(thumb_mask).rounded_rectangle((0, 0, thumb_size[0], thumb_size[1]), radius=15, fill=255)
            thumb_img.putalpha(thumb_mask)
            image.paste(thumb_img, (260, 195), thumb_img)

        channel = (song.channel_name or "")[:20]
        title = (song.title or "")[:30]

        draw = ImageDraw.Draw(image)
        
        # Thumbnail ရဲ့ ညာဘက်ခြမ်းတွင် Channel နှင့် Title ပေါ်စေရန်[span_3](start_span)[span_3](end_span)
        draw.text(
            xy=(390, 200),
            text=channel,
            font=self.font2,
            fill=(180, 180, 180),
        )
        draw.text((390, 230), title, font=self.font1, fill=self.fill)

        temp = output.with_name(f"{output.stem}.tmp{output.suffix}")
        image.convert("RGB").save(
            temp,
            format="JPEG",
            quality=90,
            subsampling=2,
        )
        temp.replace(output)
