# Warehouse bot — spec (decided)

The warehouse bot monitors storage and places reorders automatically.

## Rules

1. When CPU usage exceeds 80% for 5 continuous minutes, scale up the cluster by
   one node. When CPU usage stays below 40% for 30 minutes, scale down by one
   node. At any other usage level, the cluster is left unchanged.
2. When a delivery is more than 24 hours late, notify the customer by email.
3. The retention policy is 90 days for events and 3 years for invoices.
4. When the temperature exceeds 45°C continuously for 10 minutes, call the
   cooling handler. Below that, the cooling handler stays idle.
5. Inbound orders are validated, then saved; if validation fails, the order is
   rejected with the error stored to the log table.
6. Failed jobs are retried up to 3 times with exponential backoff, then moved
   to the dead-letter queue.
7. When the queue backlog exceeds 5000 messages, notify the on-call staff by
   pager. Otherwise, no notification is sent.
8. When a storage account exceeds 95% of its quota, warn the account owner by
   email and block new allocations. Under 95%, allocations proceed normally.
9. Three delivery partners have signed contracts that remain valid through the
   end of the year.
10. When a user reports a bug, create a ticket with the report text, the user's
   id and the timestamp, and set its status to "new".

## Constraints

- Monthly infrastructure budget is 2000 €.
- The reporting module aggregates volumes, times and costs per warehouse.