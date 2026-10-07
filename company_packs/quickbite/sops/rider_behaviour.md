---
id: rider_behaviour
title: Rider behaviour complaint
applies_to: [rider_behaviour]
keywords: [rude, rider, delivery guy, delivery boy, delivery person, behaviour, behavior, shouted, threw, misbehaved]
systems: [ops, support]
actions: [ops.read_order, ops.raise_incident, support.reply_to_customer, support.update_ticket]
success_criteria:
  - An incident against the order's rider exists with category rude_behaviour (or the closest category) and a factual description.
  - No refund or coupon was issued (behaviour complaints are not compensated with money).
  - The reply apologises and confirms the report was raised, without sharing the rider's personal details.
  - The ticket is categorised rider_behaviour and resolved.
---
# Rider behaviour complaint

1. In **Ops Admin**, open the order and identify the **rider**.
2. Raise an incident against the rider with the best-matching category and a
   factual description of what the customer reported.
3. Do not offer money. Apologise sincerely and confirm that the fleet team
   will review it.
4. Never share the rider's phone number or other personal details.
5. Reply, then resolve the ticket.
