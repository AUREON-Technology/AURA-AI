-- Reference pilot "ecommerce" (Track AP, AP3): a small shop in a non-public schema.
-- Exercises: a schema other than public, enum-typed status, a composite primary key and a
-- composite foreign key onto it, a quoted mixed-case table, CHECKs, and role grants.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'shop_support') THEN
        CREATE ROLE shop_support NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'shop_ops') THEN
        CREATE ROLE shop_ops NOLOGIN;
    END IF;
END
$$;

CREATE SCHEMA shop;

CREATE TYPE shop.order_status AS ENUM ('placed', 'paid', 'shipped', 'delivered', 'cancelled', 'returned');

CREATE TABLE shop.customers (
    id          integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email       text NOT NULL UNIQUE,
    full_name   text NOT NULL,
    country     text NOT NULL,
    created_at  timestamptz NOT NULL
);
COMMENT ON TABLE shop.customers IS 'People who have placed at least one order.';

CREATE TABLE shop.products (
    sku       text PRIMARY KEY CHECK (sku ~ '^[A-Z]{3}-[0-9]{4}$'),
    title     text NOT NULL,
    category  text NOT NULL,
    price     numeric(10, 2) NOT NULL CHECK (price > 0),
    stock     integer NOT NULL CHECK (stock >= 0)
);

CREATE TABLE shop.orders (
    id           integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id  integer NOT NULL REFERENCES shop.customers (id),
    status       shop.order_status NOT NULL DEFAULT 'placed',
    placed_at    timestamptz NOT NULL,
    total        numeric(12, 2) NOT NULL CHECK (total >= 0)
);
COMMENT ON TABLE shop.orders IS 'Customer orders and their fulfilment state.';

CREATE TABLE shop.order_items (
    order_id    integer NOT NULL REFERENCES shop.orders (id),
    sku         text NOT NULL REFERENCES shop.products (sku),
    qty         integer NOT NULL CHECK (qty > 0),
    unit_price  numeric(10, 2) NOT NULL,
    PRIMARY KEY (order_id, sku)
);

CREATE TABLE shop.payments (
    id        integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id  integer NOT NULL REFERENCES shop.orders (id),
    method    text NOT NULL CHECK (method IN ('card', 'paypal', 'bank_transfer')),
    amount    numeric(12, 2) NOT NULL,
    paid_at   timestamptz NOT NULL
);

CREATE TABLE shop.shipments (
    id             integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id       integer NOT NULL UNIQUE REFERENCES shop.orders (id),
    carrier        text NOT NULL,
    tracking_url   text,
    shipped_at     timestamptz NOT NULL,
    delivered_at   timestamptz
);

CREATE TABLE shop.returns (
    id          integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id    integer NOT NULL,
    sku         text NOT NULL,
    reason      text NOT NULL,
    created_at  timestamptz NOT NULL,
    CONSTRAINT returns_item_fk FOREIGN KEY (order_id, sku) REFERENCES shop.order_items (order_id, sku)
);

CREATE TABLE shop."SupplierContacts" (
    "SupplierName"  text PRIMARY KEY,
    "ContactEmail"  text NOT NULL
);
COMMENT ON TABLE shop."SupplierContacts" IS 'Imported from a spreadsheet; keeps its original casing.';

GRANT USAGE ON SCHEMA shop TO shop_support, shop_ops;
GRANT SELECT ON shop.customers, shop.orders, shop.order_items, shop.shipments TO shop_support;
GRANT SELECT, UPDATE ON shop.orders TO shop_ops;
GRANT SELECT, INSERT ON shop.shipments, shop.returns TO shop_ops;
