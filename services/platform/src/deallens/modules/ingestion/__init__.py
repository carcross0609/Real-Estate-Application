"""PROPERTY ingestion bounded context (§9.3): source adapters, raw payload store,
normalization, entity resolution, listing events. Nothing else writes listings/properties;
emits `ListingUpserted`/`ListingChanged` domain events.
"""
