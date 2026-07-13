"""ALERTS bounded context (§9.3): buy-box matching, watchlists, notification dispatch,
digests. Consumes `ScoreUpdated`/`ListingChanged`. Buy boxes are indexed by market so a
score update only re-evaluates boxes for that market (§11.5 query #6).
"""
