---
id: food_quality
title: Food quality (cold, spilled, damaged, burnt)
applies_to: [food_quality]
keywords: [cold, spilled, leaked, burnt, stale, damaged, soggy, inedible, bad quality]
systems: [ops, payments, support]
actions: [ops.read_order, ops.raise_incident, payments.read, payments.refund, support.reply_to_customer, support.update_ticket]
success_criteria:
  - A refund of 50% of the affected item's price exists, with reason food_quality.
  - An incident with category food_quality exists against the restaurant for this order.
  - The ticket is categorised food_quality and resolved.
---
# Food quality

1. Identify the **affected item(s)** on the order in Ops Admin.
2. Refund **50% of the affected item's price** with reason `food_quality`.
   If the customer describes the whole order as inedible, the remedy is
   off-policy and needs approval.
3. Raise an incident against the **restaurant** with category `food_quality`.
4. Confirm both records, reply with the refund id, and resolve the ticket.
