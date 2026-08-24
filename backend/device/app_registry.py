from typing import Optional

# Maps the short app_name identifiers used throughout this project (Explore/
# Deploy's app_name, app_cards.json's keys, the KB's per-app namespace) to
# real Android package names, so the agent loop can launch the target app
# before reasoning about its screen instead of assuming it's already open.
_KNOWN_PACKAGES = {
    "youtube": "com.google.android.youtube",
    "gmail": "com.google.android.gm",
    "mail": "com.google.android.gm",
    "linkedin": "com.linkedin.android",
    "chrome": "com.android.chrome",
    "twitter": "com.twitter.android",
    "x": "com.twitter.android",
    "settings": "com.android.settings",
    "whatsapp": "com.whatsapp",
    "instagram": "com.instagram.android",
    "maps": "com.google.android.apps.maps",
    "messages": "com.google.android.apps.messaging",
    "playstore": "com.android.vending",
    "play store": "com.android.vending",
    "calendar": "com.google.android.calendar",
    "photos": "com.google.android.apps.photos",
    "claude": "com.anthropic.claude",
}


def resolve_package(app_name: str) -> Optional[str]:
    """Look up the Android package for a known app_name, or None if unknown.

    Unknown app_name values are not an error — the caller falls back to
    starting from whatever screen is currently active (the pre-existing
    behavior), which still works if the user already had the app open.
    """
    return _KNOWN_PACKAGES.get(app_name.strip().lower())
