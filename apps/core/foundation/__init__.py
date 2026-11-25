"""Shared foundation for workspace modules.

Five small pieces every module uses instead of rolling its own:
permissions (object level, default deny), storage (interface only), search hook,
jobs (thin layer over the transactional outbox) and notifications + audit helpers.
"""
