import asyncio
import random
from typing import Optional, Union

import yt_dlp

from melody import logger
from melody.helpers._dataclass import Media, Track
from melody.helpers._utilities import Utilities

utils = Utilities()

MediaItem = Union[Media, Track]

AUTOPLAY_TAG = "𝐀‌υᴛσᴘʟᴧʏ 🟢"
MAX_HISTORY = 200  # cap per-chat "played" memory so it never grows unbounded


class _MixLogger:
    def debug(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


def _fmt_duration(seconds) -> str:
    if not seconds:
        return "00:00"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class Autoplay:
    """Finds a related track to keep the stream going once the queue ends."""

    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self._played: dict[int, set[str]] = {}
        self._pool: dict[int, list[dict]] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def reset(self, chat_id: int) -> None:
        self._played.pop(chat_id, None)
        self._pool.pop(chat_id, None)
        self._locks.pop(chat_id, None)

    def _lock_for(self, chat_id: int) -> asyncio.Lock:
        return self._locks.setdefault(chat_id, asyncio.Lock())

    def _mark_played(self, chat_id: int, video_id: str) -> set[str]:
        seen = self._played.setdefault(chat_id, set())
        seen.add(video_id)
        if len(seen) > MAX_HISTORY:
            self._played[chat_id] = set(random.sample(list(seen), MAX_HISTORY // 2))
        return self._played[chat_id]

    def _extract_mix_sync(self, video_id: str) -> list[dict]:
        mix_url = f"{self.base}{video_id}&list=RD{video_id}"
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "skip_download": True,
            "playlistend": 30,
            "logger": _MixLogger(),
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(mix_url, download=False)
        entries = (info or {}).get("entries") or []

        videos = []
        for data in entries:
            if not data or not data.get("id"):
                continue
            videos.append(
                {
                    "id": data["id"],
                    "title": data.get("title"),
                    "duration": _fmt_duration(data.get("duration")),
                    "channel": {"name": data.get("channel") or data.get("uploader") or ""},
                    "thumbnails": data.get("thumbnails") or [],
                    "link": self.base + data["id"],
                    "viewCount": {"short": ""},
                }
            )
        return videos

    async def _fetch_mix(self, video_id: str) -> list[dict]:
        try:
            return await asyncio.to_thread(self._extract_mix_sync, video_id)
        except Exception as e:
            logger.error(f"[Autoplay] Mix lookup failed for {video_id}: {e}")
            return []

    async def _from_search(self, video_id: str, title: str, video: bool) -> Optional[Track]:
        from melody import yt

        try:
            track = await yt.search(title, 0, video=video)
        except Exception as e:
            logger.error(f"[Autoplay] Search fallback failed for {video_id}: {e}")
            return None

        if track and track.id != video_id:
            track.user = AUTOPLAY_TAG
            return track
        return None

    def _to_track(self, data: dict, video: bool) -> Track:
        thumbnails = data.get("thumbnails") or [{}]
        thumb_url = (thumbnails[-1].get("url") or "").split("?")[0]
        duration = data.get("duration")

        return Track(
            id=data.get("id"),
            channel_name=(data.get("channel") or {}).get("name"),
            duration=duration,
            duration_sec=utils.to_seconds(duration) if duration else 0,
            title=(data.get("title") or "Unknown")[:25],
            thumbnail=thumb_url,
            url=data.get("link"),
            user=AUTOPLAY_TAG,
            view_count=(data.get("viewCount") or {}).get("short"),
            video=video,
        )

    async def get_related(
        self, chat_id: int, last: MediaItem, video: bool = False
    ) -> Optional[Track]:
        if not last or not getattr(last, "id", None):
            return None

        video_id = last.id
        title = getattr(last, "title", None)

        async with self._lock_for(chat_id):
            seen = self._mark_played(chat_id, video_id)

            pool = [v for v in self._pool.get(chat_id, []) if v["id"] not in seen]

            if not pool:
                videos = await self._fetch_mix(video_id)
                self._pool[chat_id] = videos
                pool = [v for v in videos if v["id"] not in seen]

            if pool:
                data = random.choice(pool)
                self._mark_played(chat_id, data["id"])
                return self._to_track(data, video)

            if title:
                return await self._from_search(video_id, title, video)

            return None

autoplay = Autoplay()
