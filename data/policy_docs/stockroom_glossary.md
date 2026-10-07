# Stockroom Glossary

**Document ID:** DOC-GLO-001 · **Effective:** 1 March 2026 · **Owner:** Inventory Planning

- **SKU** — Stock Keeping Unit. Every product has an ID in the format `SKU-####` (for example
  `SKU-1015`). Variants append a suffix such as `-V2`.
- **Stock level** — the number of sellable units physically in the warehouse right now. It excludes
  units reserved for open orders.
- **Reorder point** — the stock level at or below which a restock request should be raised.
- **Lead time** — the number of business days between placing a restock request and receiving the
  goods, as published in the supplier lead-time schedule.
- **Backordered** — an order status meaning at least one line could not be fulfilled from stock.
- **Legacy shard** — orders with IDs `ORD-9000` and above live in the older order system that was
  migrated in 2025. Lookups against the legacy shard can be slower and occasionally time out; the
  recommended practice is to retry once after a short pause.
- **RA number** — Returns Authorisation number issued by Customer Operations.
- **RSR** — Restock Request, ID format `RSR-####`.
- **Business day** — Monday to Friday excluding public holidays at the dispatching warehouse.
- **Location** — `aisle` and `bin` identify where a product is stored. Aisles are numbered from the
  loading dock; bins are numbered from the floor upwards.
