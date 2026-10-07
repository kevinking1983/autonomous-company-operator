---
id: payment_issue
title: Payment issues (charged for a cancelled order, double charge)
applies_to: [payment_issue]
keywords: [charged, debited, deducted, money, double, twice, cancelled but, refund not received, payment]
systems: [ops, payments, support]
actions: [ops.read_order, payments.read, payments.refund, support.reply_to_customer, support.update_ticket]
success_criteria:
  - "The total refunded for the order equals what policy requires: every captured rupee for a cancelled order, or exactly the duplicate payment(s) for a double charge."
  - No refund was created when an equivalent refund already existed.
  - The customer's reply quotes the refund id(s) and the expected settlement date.
  - The ticket is categorised payment_issue and resolved.
---
# Payment issues

## Charged for a cancelled order
1. In **Ops Admin**, confirm the order is `cancelled` and note who cancelled it.
2. In **Payments**, list the order's payments **and the refunds already on
   them**. Cancelled orders are usually refunded automatically
   (`system:auto-refund`).
   - **A refund already exists:** do not refund again. Tell the customer the
     refund id and its expected date.
   - **No refund exists:** refund the full payment with reason
     `order_cancelled`.

## Double charge
1. In **Payments**, search by the order id. Two or more `captured` payments for
   one order means a double charge.
2. Keep the first payment. Refund each duplicate in full with reason
   `duplicate_charge`. Reversals always need supervisor approval.

In both cases, confirm the refund(s) exist, reply with the ids, and resolve
the ticket.
