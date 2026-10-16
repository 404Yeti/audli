"""Official YouTube Data API only. Metadata never enters an LLM prompt."""
from datetime import datetime, timezone
import re
import logging
import time
import httpx
from pydantic import ValidationError
from app.homework import Capabilities, ListeningContent, SearchPlan, DiscoveryUnavailable, relevance

class _DiscoveryLogFilter(logging.Filter):
    def filter(self, record):
        # HTTPX's INFO request logs otherwise include the learner's topic in q.
        return 'www.googleapis.com/youtube/v3/' not in record.getMessage()


logging.getLogger('httpx').addFilter(_DiscoveryLogFilter())

VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
DURATION = re.compile(r'^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$')
WARNING = re.compile(r'\b(explicit|porn|sexual|suicide|self.harm|gore|graphic violence|fuck|shit)\b', re.I)


def youtube_url(video_id: str) -> str:
    if not VIDEO_ID.fullmatch(video_id):
        raise ValueError('Invalid YouTube ID')
    return 'https://www.youtube.com/watch?v=' + video_id


def valid_url(content: ListeningContent) -> bool:
    return content.provider == 'youtube' and bool(VIDEO_ID.fullmatch(content.provider_id)) and content.canonical_url == youtube_url(content.provider_id)


def duration_seconds(value):
    match = DURATION.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ValueError('Invalid duration')
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    total = days * 86400 + hours * 3600 + minutes * 60 + seconds
    if total <= 0:
        raise ValueError('Invalid duration')
    return total


def parse_video(item, topic):
    try:
        snippet, details, status = item['snippet'], item['contentDetails'], item['status']
        video_id = item['id']
        restrictions = details.get('regionRestriction', {})
        title = snippet['title'].strip()
        publisher = snippet['channelTitle'].strip()
        if not title or not publisher or len(title) > 200 or len(publisher) > 200:
            return None
        return ListeningContent(provider='youtube', provider_id=video_id, canonical_url=youtube_url(video_id),
            title=title, summary=snippet.get('description', '')[:1000], topic=topic,
            language=snippet.get('defaultAudioLanguage'), duration_seconds=duration_seconds(details['duration']),
            publisher=publisher, transcript_available=details.get('caption') == 'true',
            capabilities=Capabilities(), validated_at=datetime.now(timezone.utc),
            public=status.get('privacyStatus') == 'public', processed=status.get('uploadStatus') == 'processed',
            live=snippet.get('liveBroadcastContent', 'none') != 'none',
            age_restricted=bool(details.get('contentRating', {})),
            region_allowed=restrictions.get('allowed', []), region_blocked=restrictions.get('blocked', []))
    except (KeyError, TypeError, ValueError, AttributeError, ValidationError):
        return None


class YouTubeProvider:
    def __init__(self, api_key, client=None):
        self.key = api_key
        self.client = client
        self.cooldown_until = 0.0

    def capabilities(self):
        # V0.1 deliberately offers only external playback. Caption presence is
        # not permission to retrieve text or fact-check a learner's reflection.
        return Capabilities()

    async def request(self, resource, params):
        if resource not in ('search', 'videos') or not self.key or time.monotonic() < self.cooldown_until:
            raise DiscoveryUnavailable()
        async def get(client):
            response = await client.get('https://www.googleapis.com/youtube/v3/' + resource,
                params=params, headers={'X-Goog-Api-Key': self.key}, follow_redirects=False)
            if response.status_code in (403, 429):
                self.cooldown_until = time.monotonic() + 900
            if response.status_code != 200 or len(response.content) > 512_000:
                raise DiscoveryUnavailable()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
                raise DiscoveryUnavailable()
            return payload['items']
        try:
            if self.client is not None:
                return await get(self.client)
            async with httpx.AsyncClient(timeout=httpx.Timeout(4, connect=2), follow_redirects=False) as client:
                return await get(client)
        except (httpx.HTTPError, ValueError, TypeError):
            raise DiscoveryUnavailable() from None

    async def search(self, plan: SearchPlan):
        items = await self.request('search', {'part': 'snippet', 'type': 'video', 'q': plan.queries[0],
            'maxResults': 10, 'safeSearch': 'strict', 'relevanceLanguage': 'en',
            **({'regionCode': plan.region} if plan.region else {})})
        ids = []
        for item in items[:10]:
            identifier = item.get('id', {}).get('videoId') if isinstance(item, dict) and isinstance(item.get('id'), dict) else None
            if isinstance(identifier, str) and VIDEO_ID.fullmatch(identifier) and identifier not in ids:
                ids.append(identifier)
        return ids

    async def metadata(self, ids, topic):
        if not ids:
            return []
        if len(ids) > 10 or any(not VIDEO_ID.fullmatch(value) for value in ids):
            raise DiscoveryUnavailable()
        items = await self.request('videos', {'part': 'snippet,contentDetails,status', 'id': ','.join(ids)})
        return [content for item in items[:10] if isinstance(item, dict) and item.get('id') in ids
            and (content := parse_video(item, topic)) is not None]

    def validate(self, content, plan):
        if not valid_url(content) or content.availability != 'available':
            return False
        if not content.public or not content.processed or content.live or content.age_restricted:
            return False
        if not plan.min_seconds <= content.duration_seconds <= plan.max_seconds:
            return False
        if not content.language or content.language.lower().split('-')[0] != 'en':
            return False
        if content.region_allowed and (not plan.region or plan.region not in content.region_allowed):
            return False
        if content.region_blocked and (not plan.region or plan.region in content.region_blocked):
            return False
        return relevance(content, plan) > 0 and not WARNING.search(content.title + ' ' + content.summary)

