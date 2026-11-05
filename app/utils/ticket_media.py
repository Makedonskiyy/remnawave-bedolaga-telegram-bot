"""Normalize ticket galleries for Telegram, preserving legacy single attachments."""

from itertools import groupby


def get_ticket_media(message) -> list[dict[str, str]]:
    items = getattr(message, 'media_items', None)
    if not items:
        items = [{'type': getattr(message, 'media_type', None), 'file_id': getattr(message, 'media_file_id', None)}]
    return [
        item
        for item in items
        if isinstance(item, dict) and item.get('type') in {'photo', 'video', 'document', 'voice'} and item.get('file_id')
    ]


def ticket_media_chunks(items: list[dict[str, str]]):
    # Telegram cannot mix documents with photos/videos in one album, and voices are never albums. Keep order.
    for kind, group in groupby(items, key=lambda item: item['type'] if item['type'] in {'document', 'voice'} else 'visual'):
        batch = list(group)
        size = 1 if kind != 'visual' else 10
        for offset in range(0, len(batch), size):
            yield batch[offset : offset + size]
