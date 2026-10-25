"""Resolver worker: turns YouTube Music videoIds into direct audio stream URLs.

Runs as its own process (``python -m ytm_resolver``) so yt-dlp's memory and CPU stay
out of the player. It depends only on the standard library and yt-dlp.
"""
