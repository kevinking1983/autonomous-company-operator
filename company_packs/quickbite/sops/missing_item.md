---
id: missing_item
title: Missing item
applies_to: [missing_item]
keywords: [missing, not in the bag, wasn't in, was not in, weren't in, didn't get, didn't receive, forgot, left out]
systems: [ops, payments, support]
actions: [ops.read_order, payments.read, payments.refund, support.reply_to_customer, support.update_ticket]
success_criteria:
  - A refund equal to the missing item's price (quantity x unit price) exists for the order's payment, with reason missing_item.
  - No other refund was created for this claim.
  - The customer's reply quotes the refund id and the settlement time.
  - The ticket is categorised missing_item and resolved.
---
# Missing item

1. In **Ops Admin**, open the order. Confirm the item the customer names is on
   the order, and note its quantity and unit price.
2. Check the **packing log**:
   - If the item is marked **NOT PACKED**, the claim is confirmed.
   - If it is marked packed, the claim is still usually honoured, but say so in
     the internal note. Customer history decides whether approval is needed.
3. Check the customer's **claim history**. Three or more claims in 30 days
   means the refund needs approval (repeat claimant).
4. In **Payments**, open the order's payment. **Check the refunds already on
   it.** If this item has already been refunded, do not refund again; tell the
   customer the existing refund id.
5. Refund the item's price with reason `missing_item`. Above ₹500 needs
   approval.
6. Re-open the refund and confirm the amount and reason.
7. Reply with the refund id and the settlement time (5–7 business days), then
   resolve the ticket.
