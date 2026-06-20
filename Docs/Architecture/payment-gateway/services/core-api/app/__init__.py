"""core-api application package.

Bootstrap stage exposes only health endpoints and the DB + Redis pools.
Domain packages (identity, wallet, payment, ledger, fraud, settlement,
dispute, merchant) will be added by the implementing developer.
"""
