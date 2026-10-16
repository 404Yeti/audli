"""Owner-operated live smoke probe. No DB writes, retries, flags or configuration changes.

Use only an explicitly authorized nonproduction AUDLI_YOUTUBE_API_KEY.
One search.list (100 units) and at most one videos.list (1 unit).
"""
import argparse
import asyncio
import os
from app.homework import SearchPlan, DiscoveryUnavailable
from app.services.youtube import YouTubeProvider


async def probe(key):
    provider = YouTubeProvider(key)
    plan = SearchPlan(topic='science', queries=['science simple English short explanation'], practice_level='B1')
    try:
        ids = await asyncio.wait_for(provider.search(plan), timeout=5)
        items = await asyncio.wait_for(provider.metadata(ids, plan.topic), timeout=5)
    except (DiscoveryUnavailable, TimeoutError):
        print('LIVE discovery failed or quota/credentials unavailable. No assignment was created.')
        return 1
    eligible = [item for item in items if provider.validate(item, plan)]
    print(f'LIVE API returned {len(ids)} IDs, {len(items)} parsed records, {len(eligible)} eligible candidates.')
    if not eligible:
        print('No eligible candidate; suitability acceptance remains unverified.')
        return 1
    # IDs and URLs came from the adapter; never display arbitrary external prose.
    print('Verified canonical link:', eligible[0].canonical_url)
    print('Manual playback/content suitability and spoken journey acceptance are still required.')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Authorize up to 101 YouTube quota units with the provided nonproduction key')
    args = parser.parse_args()
    key = os.environ.get('AUDLI_YOUTUBE_API_KEY', '').strip()
    if not args.live or not key:
        print('NOT RUN: --live and an authorized nonproduction AUDLI_YOUTUBE_API_KEY are required.')
        return 2
    return asyncio.run(probe(key))


if __name__ == '__main__':
    raise SystemExit(main())
