"""Built-in test tracks: playable signed out, and used by tools/measure.py (--autoplay)."""

from __future__ import annotations

from ytm_lite.app.models import AlbumRef, ArtistRef, Track

TEST_TRACKS = (
    Track(
        "4D7u5KF7SP8",
        "Get Lucky (feat. Pharrell Williams and Nile Rodgers)",
        (ArtistRef("Daft Punk"), ArtistRef("Pharrell Williams"), ArtistRef("Nile Rodgers")),
        AlbumRef("Random Access Memories"),
        370,
    ),
    Track(
        "BSTsnWoslP4",
        "Bohemian Rhapsody",
        (ArtistRef("Queen"),),
        AlbumRef("A Night At The Opera"),
        355,
    ),
    Track("NMRhx71bGo4", "Let It Happen", (ArtistRef("Tame Impala"),), AlbumRef("Currents"), 468),
    Track("OF4rw3LOfqY", "Echoes", (ArtistRef("Pink Floyd"),), AlbumRef("Meddle"), 1413),
)
