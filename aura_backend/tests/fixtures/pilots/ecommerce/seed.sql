-- Deterministic seed for the "ecommerce" pilot (no random(), no now() in values).

INSERT INTO shop.customers (email, full_name, country, created_at)
SELECT 'customer' || g || '@shop.example',
       'Customer ' || g,
       (ARRAY['DE', 'FR', 'EG', 'US', 'GB'])[1 + g % 5],
       timestamptz '2025-06-01 10:00:00+00' + g * interval '3 days'
  FROM generate_series(1, 25) AS g;

INSERT INTO shop.products (sku, title, category, price, stock)
SELECT (ARRAY['BOK', 'TOY', 'KIT'])[1 + g % 3] || '-' || lpad(g::text, 4, '0'),
       'Product ' || g,
       (ARRAY['books', 'toys', 'kitchen'])[1 + g % 3],
       5 + g * 2.5,
       g * 7 % 40
  FROM generate_series(1, 18) AS g;

INSERT INTO shop.orders (customer_id, status, placed_at, total)
SELECT 1 + g % 25,
       (ARRAY['placed', 'paid', 'shipped', 'delivered', 'delivered', 'cancelled', 'returned'])[1 + g % 7]::shop.order_status,
       timestamptz '2026-01-02 12:00:00+00' + g * interval '1 day',
       0
  FROM generate_series(1, 35) AS g;

INSERT INTO shop.order_items (order_id, sku, qty, unit_price)
SELECT o.id, p.sku, 1 + (o.id + p.n) % 3, p.price
  FROM shop.orders o
  JOIN (SELECT sku, price, row_number() OVER (ORDER BY sku) AS n FROM shop.products) p
    ON p.n IN (1 + o.id % 18, 1 + (o.id * 7) % 18);

UPDATE shop.orders o
   SET total = s.total
  FROM (SELECT order_id, sum(qty * unit_price) AS total FROM shop.order_items GROUP BY order_id) s
 WHERE s.order_id = o.id;

INSERT INTO shop.payments (order_id, method, amount, paid_at)
SELECT id, (ARRAY['card', 'card', 'paypal', 'bank_transfer'])[1 + id % 4], total, placed_at + interval '1 hour'
  FROM shop.orders WHERE status <> 'placed' AND status <> 'cancelled';

INSERT INTO shop.shipments (order_id, carrier, tracking_url, shipped_at, delivered_at)
SELECT id, (ARRAY['dhl', 'ups'])[1 + id % 2], 'https://track.example/' || id,
       placed_at + interval '2 days',
       CASE WHEN status IN ('delivered', 'returned') THEN placed_at + interval '5 days' END
  FROM shop.orders WHERE status IN ('shipped', 'delivered', 'returned');

INSERT INTO shop.returns (order_id, sku, reason, created_at)
SELECT i.order_id, i.sku, 'damaged in transit', o.placed_at + interval '8 days'
  FROM shop.order_items i JOIN shop.orders o ON o.id = i.order_id
 WHERE o.status = 'returned';

INSERT INTO shop."SupplierContacts" ("SupplierName", "ContactEmail")
VALUES ('Paper & Ink GmbH', 'orders@paper-ink.example'),
       ('ToyWorks Ltd', 'sales@toyworks.example');
