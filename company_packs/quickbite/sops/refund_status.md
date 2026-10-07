---
id: refund_status
title: Refund status enquiry
applies_to: [refund_status]
keywords: [where is my refund, refund status, not received refund, when will I get, promised refund]
systems: [payments, support]
actions: [payments.read, support.reply_to_customer, support.update_ticket]
success_criteria:
  - No refund, coupon or order change was made (this is a read-only request).
  - The reply states the existing refund's id, amount, status and expected date.
  - The ticket is categorised refund_status and resolved.
---
# Refund status enquiry

This is **read-only**. Do not create anything.

1. In **Payments → Refund ledger**, search by the order id.
2. Read the refund's **status** and **expected by** date.
   - `processing` and before the expected date: it is on its way. Give the
     date.
   - `processing` and past the expected date: apologise, add an internal
     note, and put the ticket `on_hold` for the payments team.
   - `completed`: it has been sent. Banks can take 1–2 days more to show it.
3. If there is **no refund at all** for the order, it was promised but never
   made. Treat the ticket under the matching issue SOP instead.
4. Reply with the refund id, amount, status and date, then resolve the ticket.
