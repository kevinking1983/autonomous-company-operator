---
id: every_ticket
title: How to handle any support ticket
applies_to: [all]
keywords: []
systems: [support, ops, payments]
actions: [support.read_ticket, support.update_ticket, support.add_internal_note, support.reply_to_customer, ops.read_order, payments.read]
success_criteria:
  - The customer has received exactly one clear reply that explains the outcome.
  - The ticket has a category and a final status (resolved, or pending_customer while waiting for an answer).
  - An internal note records what was checked and what was done, with record ids.
---
# Handling any ticket

Every ticket follows this frame. The issue-specific SOP fills in the middle.

1. **Read the whole ticket.** Read the conversation, any attachments, and the
   customer's other tickets in the side panel.
2. **Check for a duplicate.** If another ticket from the same customer covers
   the same order and the same problem, follow *Duplicate tickets* instead.
3. **Establish the facts in the systems of record.** Never act on the
   customer's description alone. The order, the timeline and the packing log
   are in Ops Admin. Charges and refunds are in Payments.
4. **Check the customer's claim history** on their Ops Admin customer page
   before giving money. Three or more claims in 30 days requires approval.
5. **Decide the remedy using the compensation policy.** If the remedy needs
   approval, request it and wait. Do not act first.
6. **Act** in the right system.
7. **Confirm it happened.** Re-open the record (refund, coupon, order,
   incident) and check it shows what you intended.
8. **Reply to the customer** following the tone guide, quoting the reference
   (refund id or coupon code) and the timeline.
9. **Write an internal note** listing what you checked and every record id you
   created.
10. **Set the category, and the status** to `resolved`. If you are waiting on
    the customer, set `pending_customer`.

If the problem is not clear from the ticket, follow *Asking the customer*.
If something fails and you cannot complete the work, leave the ticket `on_hold`
with an internal note, and escalate to the supervisor.
