"""Local reference formats shared by discovery and submission checks.

Extensions identify supported paths, not codecs, trusted bytes or platform support.
"""
MEDIA_EXTENSIONS = {
    'image': frozenset({'.png', '.jpg', '.jpeg', '.webp'}),
    'video': frozenset({'.mp4', '.mov', '.mkv', '.webm'}),
    'audio': frozenset({'.wav', '.mp3', '.m4a'}),
}

LOCAL_REFERENCE_EXTENSIONS = frozenset({'.json', '.md', '.txt', '.pdf'}).union(
    *MEDIA_EXTENSIONS.values())
