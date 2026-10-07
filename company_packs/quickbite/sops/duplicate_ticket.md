---
id: duplicate_ticket
title: Duplicate tickets
applies_to: [all]
keywords: [again, still, already reported, second time, same issue]
systems: [support, payments]
actions: [support.read_ticket, payments.read, support.update_ticket, support.reply_to_customer]
success_criteria:
  - The new ticket is linked to the earlier ticket for the same order and problem.
  - No new refund or coupon was created for a problem that was already compensated.
  - The reply points the customer to the existing resolution (refund id and date).
  - The ticket is closed.
---
# Duplicate tickets

A duplicate is a ticket about the **same order and the same problem** as an
earlier ticket from the same customer.

1. Open the earlier ticket and read how it was resolved.
2. In **Payments**, confirm the promised refund or coupon really exists.
3. Link the new ticket to the earlier one, using "duplicate of".
4. Reply with the existing refund id and its expected date. **Do not
   compensate twice.**
5. Close the new ticket.

If the earlier ticket promised something that does **not** exist in Payments,
it is not a duplicate. Handle it under the issue SOP.
