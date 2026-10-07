---
id: order_change
title: Cancel an order
applies_to: [order_change]
keywords: [cancel, cancel my order, placed by mistake, don't want, change order]
systems: [ops, payments, support]
actions: [ops.read_order, ops.cancel_order, payments.read, support.reply_to_customer, support.update_ticket]
success_criteria:
  - If the order had not been picked up, it is now cancelled with reason customer_request and an automatic refund exists.
  - If the order had already been picked up, it was NOT cancelled and the customer was told why.
  - The ticket is categorised order_change and resolved.
---
# Cancel an order

1. In **Ops Admin**, check the order **status** at the moment you act.
2. If it is `placed`, `accepted` or `preparing`, cancel it with reason
   `customer_request`. A paid order is refunded automatically. Confirm in
   Payments that the refund appears.
3. If it is `picked_up` or `delivered`, it **cannot be cancelled**. Explain
   kindly that the rider is already on the way, and give the expected arrival
   time.
4. Reply, then resolve the ticket.
