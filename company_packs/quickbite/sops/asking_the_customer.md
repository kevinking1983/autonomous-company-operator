---
id: asking_the_customer
title: Asking the customer (unclear tickets)
applies_to: [all]
keywords: [bad, not happy, issue, problem, terrible, worst, disappointed]
systems: [support]
actions: [support.read_ticket, support.reply_to_customer, support.update_ticket]
success_criteria:
  - The customer was asked one specific question that would let the ticket be resolved.
  - The ticket status is pending_customer while waiting.
  - Once the customer answered, the ticket was handled under the matching issue SOP.
---
# Asking the customer

Use this when you cannot tell **what went wrong** from the ticket and the
systems. For example, "my order was bad" with nothing unusual in the order
records.

1. Check the systems first. A NOT PACKED item or a big delay may already
   explain the complaint.
2. If it is still unclear, ask **one** short, specific question. For example:
   "Could you tell us what was wrong: was an item missing, was the food
   damaged or cold, or was it late?"
3. Set the ticket to `pending_customer`.
4. When the customer answers, continue under the SOP that matches their
   answer. It may match more than one, for example a missing item and burnt
   food.
