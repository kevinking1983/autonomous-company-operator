---
id: late_delivery
title: Late delivery
applies_to: [late_delivery]
keywords: [late, delay, delayed, took too long, hours, waiting, ETA]
systems: [ops, payments, support]
actions: [ops.read_order, payments.read, payments.issue_coupon, support.reply_to_customer, support.update_ticket]
success_criteria:
  - The delay used is the one in the Ops Admin timeline (delivered minus promised), not the customer's estimate.
  - If the delay is 15 minutes or more, exactly one coupon of the policy tier's value exists for the customer, referencing the order.
  - If the delay is under 15 minutes, no coupon was issued and the customer received an apology.
  - The ticket is categorised late_delivery and resolved.
---
# Late delivery

1. In **Ops Admin**, open the order and read the **delivery delay**
   (delivered time minus promised time).
2. **Use the system delay, not the customer's estimate.** Customers often
   round up. Compensate on the measured delay, and acknowledge their
   frustration without disputing their wording.
3. Look up the coupon tier in the compensation policy (under 15 min: none;
   15–30: ₹50; 30–60: ₹100; over 60: ₹150).
4. In **Payments → Coupons**, check whether a coupon already exists for this
   order. If not, issue one with reason `late_delivery` and the order id.
5. Re-open the coupon list filtered by the order and confirm.
6. Reply with an apology, the actual delay, and the coupon code and value.
   Then resolve the ticket.
