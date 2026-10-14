from django.db import models


class OrderStatus(models.TextChoices):
    PLACED = "placed", "Placed"
    PAID = "paid", "Paid"
    SHIPPED = "shipped", "Shipped"
    DELIVERED = "delivered", "Delivered"
    CANCELLED = "cancelled", "Cancelled"
    RETURNED = "returned", "Returned"


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True


class Customer(models.Model):
    """People who have placed at least one order."""

    email = models.EmailField(unique=True)
    full_name = models.TextField()
    country = models.CharField(max_length=2)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "customers"


class Product(models.Model):
    sku = models.CharField(max_length=8, primary_key=True)
    title = models.TextField()
    category = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.PositiveIntegerField()

    class Meta:
        db_table = "products"


class Order(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT)
    status = models.CharField(max_length=16, choices=OrderStatus.choices, default=OrderStatus.PLACED)
    placed_at = models.DateTimeField()
    total = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        db_table = "orders"


class Payment(models.Model):
    order = models.ForeignKey("Order", on_delete=models.PROTECT)
    method = models.CharField(max_length=16, choices=[("card", "Card"), ("paypal", "PayPal"),
                                                     ("bank_transfer", "Bank transfer")])
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    paid_at = models.DateTimeField()


class Shipment(models.Model):
    order = models.OneToOneField(Order, on_delete=models.PROTECT)
    carrier = models.TextField()
    tracking_url = models.URLField(null=True)
    shipped_at = models.DateTimeField()
    delivered_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "shipments"
