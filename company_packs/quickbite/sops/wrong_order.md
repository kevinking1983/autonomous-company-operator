---
id: wrong_order
title: Wrong order delivered
applies_to: [wrong_order]
keywords: [wrong order, someone else's, not my order, different food, wrong food, wrong bag]
systems: [support, ops, payments]
actions: [support.read_ticket, ops.read_order, ops.raise_incident, payments.read, payments.refund, support.reply_to_customer, support.update_ticket]
success_criteria:
  - The evidence (photo or description) was compared with the ordered items, and the mismatch is recorded in an internal note.
  - A full refund of the order exists, created only after supervisor approval.
  - An incident with category wrong_order exists against the restaurant for this order.
  - The customer's reply does not mention the other customer's name or order details.
  - The ticket is categorised wrong_order and resolved.
---
# Wrong order delivered

1. Look at the **attachment**. Bag stickers show the order id, customer name
   and items. Compare them with the items on the customer's order in Ops Admin.
2. If the sticker shows a **different order id**, look that order up. If it
   was delivered by the same rider around the same time, the bags were
   swapped. Record this in the internal note.
3. **Privacy:** never tell the customer whose order they received.
4. A wrong order gets a **full refund**, which always needs supervisor
   approval. Request approval with the evidence.
5. Once approved, refund the full payment with reason `wrong_order`.
6. Raise an incident against the **restaurant** with category `wrong_order`,
   describing the swap. If it was a rider swap, mention the rider.
7. Confirm the refund and the incident, reply with the refund id, and resolve
   the ticket.
