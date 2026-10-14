from flask import Blueprint

bp = Blueprint("shop", __name__, url_prefix="/shop")


@bp.get("/customers")
def customers():
    """List customers."""


@bp.route("/orders", methods=["GET", "POST"])
def orders():
    """List or place orders."""


@bp.route("/orders/<int:order_id>")
def order(order_id):
    """One order with its items."""


@bp.route("/orders/<int:order_id>/cancel", methods=["POST"])
def cancel(order_id):
    """Cancel an order that has not shipped."""
