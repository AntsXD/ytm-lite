"""Worker process: everything that talks to YouTube.

It resolves stream URLs (yt-dlp) and makes YouTube Music API calls (ytmusicapi) for the
app, which talks to it over stdin/stdout (``python -I -m ytm_worker``). Both libraries,
their dependencies and the heap churn of their big JSON responses stay out of the player
process, and the worker exits when idle so that memory goes back to the OS.
"""
