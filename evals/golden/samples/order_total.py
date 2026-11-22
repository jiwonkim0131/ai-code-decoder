def order_total(items, discount_rate=0.0):
    subtotal = sum(item["price"] * item["qty"] for item in items)
    return round(subtotal * (1 - discount_rate), 2)
