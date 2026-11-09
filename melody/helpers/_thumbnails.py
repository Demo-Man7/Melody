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
        self.font_n = ImageFont.truetype("melody/helpers/Raleway-Bold.ttf", 20)  # Channel
        self.font_t = ImageFont.truetype("melody/helpers/Raleway-Bold.ttf", 32)  # Title
        
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
            
            # 1. နောက်ခံကို ကြီးချဲ့၍ Blur လုပ်ခြင်း
            image = ImageOps.fit(
                source,
                size,
                method=Image.Resampling.BILINEAR,
                centering=(0.5, 0.5),
            ).filter(ImageFilter.GaussianBlur(25))
            
            # နောက်ခံကို အနည်းငယ် မှောင်စေရန်
            image = ImageEnhance.Brightness(image).enhance(0.5)

            # 2. controls.png ပုံကို မျက်နှာပြင်ပေါ်တွင် အလယ်ဗဟိုကျကျ တိုက်ရိုက် paste လုပ်ရန်[span_2](start_span)[span_2](end_span)
            try:
                controls = Image.open("melody/helpers/controls.png").convert("RGBA")
                # controls ပုံ၏ မူလအရွယ်အစားအတိုင်း သို့မဟုတ် မျက်နှာပြင်နှင့် အနေတော်ဖြစ်စေရန် (လိုအပ်ပါက resize လုပ်နိုင်သည်)
                image.paste(controls, (0, 0), controls)
            except Exception as e:
                logger.error(f"Controls overlay error: {e}")

            # 3. သီချင်း Thumbnail ပုံငယ်ကို ကတ်ပြားပေါ်ရှိ နေရာလွတ်တွင် ထောင့်ဝိုင်းပုံစံဖြင့် ထည့်ရန်[span_3](start_span)[span_3](end_span)
            thumb_size = 130
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
            ImageDraw.Draw(thumb_mask).rounded_rectangle((0, 0, thumb_size, thumb_size), radius=20, fill=255)

            rounded = ImageOps.fit(resize, (thumb_size, thumb_size))
            rounded.putalpha(thumb_mask)

            # Thumbnail paste မည့်နေရာ (controls.png ပုံစံပေါ်မူတည်၍ ချိန်ညှိထားသည်)[span_4](start_span)[span_4](end_span)
            image.paste(rounded, (280, 180), rounded)

        # စာသားများ သတ်မှတ်ခြင်း
        channel = (song.channel_name or "Myanmar Music")[:22]
        title = (song.title or "Unknown")[:25]

        draw = ImageDraw.Draw(image)
        
        # 4. Thumbnail ၏ ညာဘက်ခြမ်းတွင် Channel နှင့် Title ကို နေရာတကျ ဖော်ပြခြင်း[span_5](start_span)[span_5](end_span)
        draw.text((430, 190), channel, (180, 180, 180), font=self.font_n)
        draw.text((430, 220), title, (255, 255, 255), font=self.font_t)

        temp = output.with_name(f"{output.stem}.tmp{output.suffix}")
        image.convert("RGB").save(
            temp,
            format="JPEG",
            quality=90,
            subsampling=2,
        )
        temp.replace(output)
